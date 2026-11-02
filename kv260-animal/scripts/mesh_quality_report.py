#!/usr/bin/env python3
"""
网格质量客观评估：把「平滑到底有没有用」变成可打印、可复现的数字判定。

为什么需要这个工具
------------------
人眼在 WebGL 查看器（逐顶点平滑法线）下几乎分不出平滑前后；即使开平面着色，
在小尺寸屏幕上也不可靠。评审需要的是一条能复现的命令 + 一组数字，而不是"我觉得更光滑"。

本工具对同一图层的「处理前 / 处理后」两份网格计算 7 组客观指标，并给出通过/不通过判定。

指标与含义
----------
1. 二面角（相邻面法线夹角）      越小越光滑；这是人眼感知"棱角感"的直接来源
2. 顶点法线变化率（1 - 邻面法线一致性）  越小越光滑
3. 边长变异系数 CV               越小说明三角形越均匀（离散化痕迹越弱）
4. 离散平均曲率绝对值均值 |H|    越小越光滑（用均匀 Laplacian 近似）
5. 表面积变化                   过大说明网格被压塌或膨胀
6. 体积变化                     保体积算法应接近 0（Taubin 的核心卖点）
7. 退化三角面数（面积≈0）        变多说明平滑把面压扁了，是负面信号

判定规则（可用 --strict 收紧）
------------------------------
  通过条件（同时满足）：
    · 平均二面角下降 ≥ 15%
    · |体积变化| ≤ 1.0%
    · 退化面数不增加
    · 表面积变化 ≤ 5%
  否则判为"未通过"，并指出是哪一条不满足。

用法
----
  python3 mesh_quality_report.py --scene output/digimouse --layer all
  python3 mesh_quality_report.py --scene output/digimouse --layer body --markdown out.md
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def read_mesh(path: Path, n_vertices: int, n_faces: int):
    expected = 12 * n_vertices + 12 * n_faces
    actual = path.stat().st_size
    if actual != expected:
        raise ValueError(f"{path.name}: 期望 {expected} 字节，实际 {actual}")
    p = np.memmap(path, dtype="<f4", mode="r", shape=(n_vertices, 3)).astype(np.float64)
    f = np.memmap(path, dtype="<u4", mode="r", offset=12 * n_vertices, shape=(n_faces, 3))
    return p, f


def face_normals(p, f):
    v0, v1, v2 = p[f[:, 0]], p[f[:, 1]], p[f[:, 2]]
    n = np.cross(v1 - v0, v2 - v0)
    area2 = np.linalg.norm(n, axis=1)
    return n, area2


def dihedral(p, f, n_unit):
    """相邻三角面法线夹角（度）。用共享边配对。"""
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    e = np.sort(e, axis=1)
    fi = np.tile(np.arange(len(f)), 3)
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, fi = e[order], fi[order]
    same = (e[:-1, 0] == e[1:, 0]) & (e[:-1, 1] == e[1:, 1])
    a, b = fi[:-1][same], fi[1:][same]
    if len(a) == 0:
        return np.zeros(0)
    cosang = np.clip(np.einsum("ij,ij->i", n_unit[a], n_unit[b]), -1.0, 1.0)
    return np.degrees(np.arccos(cosang))


def edge_coef_var(p, f):
    """三角形三条边长度的变异系数（越小越均匀）。"""
    e1 = np.linalg.norm(p[f[:, 1]] - p[f[:, 0]], axis=1)
    e2 = np.linalg.norm(p[f[:, 2]] - p[f[:, 1]], axis=1)
    e3 = np.linalg.norm(p[f[:, 0]] - p[f[:, 2]], axis=1)
    L = np.concatenate([e1, e2, e3])
    m = L.mean()
    return float(L.std() / m) if m > 0 else 0.0


def curvature_abs_mean(p, f):
    """离散平均曲率近似：均匀 Laplacian 的模长均值。"""
    i = np.concatenate([f[:, 0], f[:, 1], f[:, 2]])
    j = np.concatenate([f[:, 1], f[:, 2], f[:, 0]])
    deg = np.bincount(i, minlength=len(p)).astype(np.float64)
    deg[deg == 0] = 1.0
    lap = np.empty((len(p), 3))
    for k in range(3):
        lap[:, k] = np.bincount(i, weights=p[j, k], minlength=len(p)) / deg - p[:, k]
    return float(np.linalg.norm(lap, axis=1).mean())


def surface_area(area2):
    return float(area2.sum() * 0.5)


def volume(p, f):
    v0, v1, v2 = p[f[:, 0]], p[f[:, 1]], p[f[:, 2]]
    return float(abs(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum()) / 6.0)


def metrics(p, f):
    n, area2 = face_normals(p, f)
    ln = np.linalg.norm(n, axis=1)
    ln[ln == 0] = 1.0
    n_unit = n / ln[:, None]
    ang = dihedral(p, f, n_unit)
    # 顶点法线一致性：每个顶点邻面法线与其平均法线的偏差
    i = np.concatenate([f[:, 0], f[:, 1], f[:, 2]])
    acc = np.zeros((len(p), 3))
    for k in range(3):
        acc[:, k] = np.bincount(i, weights=np.tile(n_unit[:, k], 3), minlength=len(p))
    accn = np.linalg.norm(acc, axis=1)
    accn[accn == 0] = 1.0
    acc = acc / accn[:, None]
    # 逐面与其三个顶点平均法线的夹角
    dev = []
    for c in range(3):
        vn = acc[f[:, c]]
        cosang = np.clip(np.einsum("ij,ij->i", n_unit, vn), -1.0, 1.0)
        dev.append(np.degrees(np.arccos(cosang)))
    normal_dev = float(np.mean(np.concatenate(dev)))

    tri_area = area2 * 0.5
    degenerate = int((tri_area < 1e-12).sum())

    return {
        "dihedral_mean": float(ang.mean()) if len(ang) else 0.0,
        "dihedral_p90": float(np.percentile(ang, 90)) if len(ang) else 0.0,
        "normal_dev": normal_dev,
        "edge_cv": edge_coef_var(p, f),
        "curvature": curvature_abs_mean(p, f),
        "area": surface_area(area2),
        "volume": volume(p, f),
        "degenerate": degenerate,
        "vertices": int(len(p)),
        "faces": int(len(f)),
    }


def verdict(a, b, min_dihedral_drop=15.0, max_vol_pct=1.0, max_area_pct=5.0):
    """a = 处理前，b = 处理后。返回 (是否通过, 原因列表)。"""
    reasons, ok = [], True
    drop = (1 - b["dihedral_mean"] / a["dihedral_mean"]) * 100 if a["dihedral_mean"] else 0.0
    vol_pct = (b["volume"] - a["volume"]) / a["volume"] * 100 if a["volume"] else 0.0
    area_pct = (b["area"] - a["area"]) / a["area"] * 100 if a["area"] else 0.0

    if drop < min_dihedral_drop:
        ok = False
        reasons.append(f"二面角仅下降 {drop:.1f}%（要求 ≥{min_dihedral_drop}%）")
    if abs(vol_pct) > max_vol_pct:
        ok = False
        reasons.append(f"体积变化 {vol_pct:+.3f}%（要求 ≤{max_vol_pct}%）")
    if abs(area_pct) > max_area_pct:
        ok = False
        reasons.append(f"表面积变化 {area_pct:+.2f}%（要求 ≤{max_area_pct}%）")
    if b["degenerate"] > a["degenerate"]:
        ok = False
        reasons.append(f"退化面增加 {a['degenerate']} → {b['degenerate']}")
    return ok, reasons, drop, vol_pct, area_pct


def main():
    ap = argparse.ArgumentParser(
        description="网格质量客观评估（处理前 vs 处理后）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--scene", type=Path, default=Path("output/digimouse"))
    ap.add_argument("--layer", default="all")
    ap.add_argument("--before-suffix", default=".display.mesh")
    ap.add_argument("--after-suffix", default=".taubin.mesh")
    ap.add_argument("--markdown", type=Path, default=None, help="把表格写入 Markdown 文件")
    ap.add_argument("--strict", action="store_true", help="收紧阈值（二面角 ≥30%）")
    ap.add_argument("--no-respect-scene", dest="respect_scene", action="store_false",
                    help="不理会 scene.json 里的 skipped 标记，强制按指标判定")
    ap.set_defaults(respect_scene=True)
    args = ap.parse_args()

    scene_path = args.scene / "scene.json"
    if not scene_path.is_file():
        raise SystemExit(f"找不到 {scene_path}")
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    ids = [l["id"] for l in scene["layers"]]
    wanted = ids if args.layer == "all" else [args.layer]

    min_drop = 30.0 if args.strict else 15.0

    lines = []
    lines.append("| 图层 | 二面角 前→后 | 下降 | 法线偏差 前→后 | 边长CV 前→后 | 曲率 前→后 | 体积变化 | 表面积变化 | 退化面 | 判定 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|:--:|")
    header = (f"{'图层':<10}{'二面角前':>9}{'后':>8}{'下降':>8}"
              f"{'法线偏差前':>11}{'后':>8}{'曲率前':>9}{'后':>8}"
              f"{'体积%':>8}{'面积%':>8}{'退化':>6}  {'判定'}")
    print(header)
    print("-" * len(header))

    passed = failed = skipped_n = 0
    for lid in wanted:
        L = next(x for x in scene["layers"] if x["id"] == lid)
        stem = Path(L["file"]).name
        for suf in (args.before_suffix, args.after_suffix, ".taubin.mesh", ".display.mesh"):
            stem = stem.replace(suf, "")
        before = args.scene / (stem + args.before_suffix)
        after = args.scene / (stem + args.after_suffix)
        if not before.is_file() or not after.is_file():
            print(f"{lid:<10}  跳过（缺少 {before.name} 或 {after.name}）")
            continue
        nv, nf = int(L["vertices"]), int(L["triangles"])
        pa, fa = read_mesh(before, nv, nf)
        pb, fb = read_mesh(after, nv, nf)
        ma, mb = metrics(pa, fa), metrics(pb, fb)
        ok, reasons, drop, vol_pct, area_pct = verdict(ma, mb, min_drop)
        # 尊重 scene.json 里「质量门判定为跳过」的图层：这类图层仍会打印指标
        # 供查阅，但不再计为失败，退出码也不因此变 1。
        was_skipped = (args.respect_scene
                       and str(L.get("smoothing", "")).startswith("skipped"))
        if was_skipped:
            skipped_n += 1
            tag = "已跳过"
        elif ok:
            passed += 1
            tag = "通过"
        else:
            failed += 1
            tag = "不通过"
        print(f"{lid:<10}{ma['dihedral_mean']:>9.2f}{mb['dihedral_mean']:>8.2f}{drop:>7.1f}%"
              f"{ma['normal_dev']:>11.2f}{mb['normal_dev']:>8.2f}"
              f"{ma['curvature']:>9.5f}{mb['curvature']:>8.5f}"
              f"{vol_pct:>+8.3f}{area_pct:>+8.2f}{ma['degenerate']:>3}/{mb['degenerate']:<3}"
              f"  {tag}")
        lines.append(
            f"| {lid} | {ma['dihedral_mean']:.2f}° → {mb['dihedral_mean']:.2f}° | **{drop:.1f}%** | "
            f"{ma['normal_dev']:.2f}° → {mb['normal_dev']:.2f}° | "
            f"{ma['edge_cv']:.4f} → {mb['edge_cv']:.4f} | "
            f"{ma['curvature']:.5f} → {mb['curvature']:.5f} | "
            f"{vol_pct:+.3f}% | {area_pct:+.2f}% | {ma['degenerate']} → {mb['degenerate']} | {tag} |")
        if not ok and not was_skipped:
            for r in reasons:
                print(f"{'':<10}  └─ {r}")

    print("-" * len(header))
    extra = f"，已跳过 {skipped_n}（质量门判定，见 scene.json）" if skipped_n else ""
    print(f"通过 {passed} / 不通过 {failed}{extra}"
          f"（阈值：二面角下降 ≥{min_drop}%，|体积| ≤1.0%，|面积| ≤5%，退化面不增加）")

    if args.markdown:
        body = "\n".join(lines)
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            "| 指标 | 含义 |\n|---|---|\n"
            "| 二面角 | 相邻三角面法线夹角，**越小越光滑**，是人眼「棱角感」的直接来源 |\n"
            "| 法线偏差 | 每个面与其顶点平均法线的夹角，**越小越光滑** |\n"
            "| 边长 CV | 三角形边长变异系数，**越小越均匀** |\n"
            "| 曲率 | 离散平均曲率的模长均值，**越小越光滑** |\n"
            "| 体积变化 | Taubin 的核心指标，应接近 0（普通 Laplacian 会收缩） |\n"
            "| 表面积变化 | 过大说明网格被压塌或膨胀 |\n"
            "| 退化面 | 面积≈0 的三角形，平滑不应使其增加 |\n\n"
            + body + "\n", encoding="utf-8")
        print(f"\n已写入 {args.markdown}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
