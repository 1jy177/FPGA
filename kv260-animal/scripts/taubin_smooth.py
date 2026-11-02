#!/usr/bin/env python3
"""
KV260 MC 网格 Taubin λ|μ 平滑（保体积，不收缩）

为什么需要它
------------
KV260 硬件流水线输出的是「原始 marching cubes 网格」。原始 MC 网格沿体素
边界生成，视觉上是阶梯状（staircase）。项目原始版本明确声明
"without mesh smoothing"，因此这一步是新增能力，不是重复劳动。

为什么用 Taubin 而不是普通 Laplacian
------------------------------------
普通 Laplacian 平滑每迭代一次就把网格整体缩小一点（收缩），多次迭代后体积
损失明显。Taubin λ|μ 交替执行一个正步（λ>0）和一个负步（μ<0），二者近似
抵消低频收缩，因此可以在保持体积的前提下反复迭代去噪。

为什么这一层留在 ARM 上（硬件-软件分工论证）
--------------------------------------------
- 扫描体素（规则网格、海量单元、无数据依赖）→ 交给 FPGA，可并行
- 网格平滑（不规则邻接、随机访问、强数据依赖）→ 留在 ARM，向量化后
  numpy 已经能跑到接近内存带宽

这正是本项目「算法-硬件协同划分」的一部分，不是「FPGA 算完再补个后处理」。

网格文件格式（无头部）
----------------------
  float32 xyz  ×  N 个顶点     （12 字节/顶点）
  uint32  ijk  ×  M 个三角面   （12 字节/面）

顶点数和面数从 scene.json 读取，不做猜测。

用法
----
  # 只处理 body 图层，输出 body.taubin.mesh，并打印指标
  python3 taubin_smooth.py --scene ../output/digimouse --layer body

  # 处理完直接替换进 scene.json（网页就能看到，因为 viewer 会校验 sha256）
  python3 taubin_smooth.py --scene ../output/digimouse --layer body --patch

  # 处理所有图层
  python3 taubin_smooth.py --scene ../output/digimouse --layer all --patch

  # 调强度
  python3 taubin_smooth.py --scene ../output/digimouse --layer body --iters 20

指标说明
--------
  rms_lap   : 每个顶点到其邻居平均位置的均方根距离（越小越光滑）
  volume_mm3: 用散度定理算的封闭网格体积（用于验证「不收缩」）
  max_move  : 单顶点最大位移（mm），用来判断是否过度平滑
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------- 读写

def read_mesh(path: Path, n_vertices: int, n_faces: int):
    """按 scene.json 给出的顶点/面数，用 memmap 读取，避免整文件进内存两次。"""
    expected = 12 * n_vertices + 12 * n_faces
    actual = path.stat().st_size
    if actual != expected:
        raise ValueError(
            f"{path.name}: 期望 {expected} 字节 "
            f"({n_vertices} 顶点 + {n_faces} 三角面)，实际 {actual} 字节。"
            "scene.json 里的数字和文件不匹配。"
        )
    points = np.memmap(path, dtype="<f4", mode="r", shape=(n_vertices, 3)).astype(np.float64)
    faces = np.memmap(path, dtype="<u4", mode="r", offset=12 * n_vertices, shape=(n_faces, 3))
    return points, faces


def write_mesh(path: Path, points: np.ndarray, faces: np.ndarray):
    with path.open("wb") as stream:
        points.astype("<f4").tofile(stream)
        faces.astype("<u4").tofile(stream)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 几何

def build_edges(faces: np.ndarray):
    """把三角面展开成有向边 (i -> j) 列表。每个三角面贡献 3 条有向边。"""
    i = np.concatenate((faces[:, 0], faces[:, 1], faces[:, 2])).astype(np.int32)
    j = np.concatenate((faces[:, 1], faces[:, 2], faces[:, 0])).astype(np.int32)
    return i, j


def degrees(i: np.ndarray, n: int) -> np.ndarray:
    deg = np.bincount(i, minlength=n).astype(np.float64)
    deg[deg == 0.0] = 1.0          # 孤立顶点不参与平滑，避免除零
    return deg


def laplacian(points: np.ndarray, i: np.ndarray, j: np.ndarray, deg: np.ndarray) -> np.ndarray:
    """返回每个顶点的 (邻居平均位置 - 自身位置)。用 bincount 做加权求和，C 速度。"""
    n = len(points)
    delta = np.empty((n, 3), dtype=np.float64)
    for k in range(3):
        neighbour_sum = np.bincount(i, weights=points[j, k], minlength=n)
        delta[:, k] = neighbour_sum / deg - points[:, k]
    return delta


def taubin(points: np.ndarray, faces: np.ndarray, lam: float, mu: float,
           iters: int, freeze: np.ndarray | None = None) -> np.ndarray:
    """
    λ|μ Taubin 平滑。每次迭代先走一步 λ>0，再走一步 μ<0。
    λ=0.5, μ=-0.53 是经典取值，通带近似平坦且不收缩。
    """
    i, j = build_edges(faces)
    raw_deg = np.bincount(i, minlength=len(points))
    deg = degrees(i, len(points))
    # 孤立顶点（没有任何三角面引用）必须冻结。
    # degrees() 把它们的度数改成 1 以避免除零，于是 laplacian 返回 -p，
    # 顶点会被拉向原点；在 λ|μ 交替下还会逐步放大，产生几十毫米的假位移。
    # 减面网格（vtkQuadricClustering）里确实会留下这种孤立顶点。
    stuck = raw_deg == 0
    freeze = stuck if freeze is None else (freeze | stuck)
    p = points.copy()
    for _ in range(iters):
        for factor in (lam, mu):
            delta = laplacian(p, i, j, deg)
            delta[freeze] = 0.0
            p += factor * delta
    return p, int(stuck.sum())


def metrics(points: np.ndarray, faces: np.ndarray):
    """返回 (rms_lap, volume)。"""
    i, j = build_edges(faces)
    deg = degrees(i, len(points))
    delta = laplacian(points, i, j, deg)
    rms = float(np.sqrt((delta ** 2).sum(axis=1).mean()))
    v0 = points[faces[:, 0]]
    v1 = points[faces[:, 1]]
    v2 = points[faces[:, 2]]
    volume = float(abs(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum()) / 6.0)
    return rms, volume


# ---------------------------------------------------------------- scene.json 回写

def patch_scene(scene_path: Path, layer_id: str, mesh_path: Path,
                points: np.ndarray, faces: np.ndarray):
    """
    viewer（scene.html）会逐个校验每个网格文件的 sha256 与 scene.json 记录一致，
    所以换掉网格文件后必须同步更新 scene.json，否则网页直接报错。
    """
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    new_hash = digest(mesh_path)
    bounds = [points.min(axis=0).tolist(), points.max(axis=0).tolist()]
    hit = False
    for layer in scene["layers"]:
        if layer["id"] != layer_id:
            continue
        hit = True
        targets = [layer]
        if isinstance(layer.get("display"), dict):
            targets.append(layer["display"])
        for target in targets:
            target["file"] = mesh_path.name
            target["sha256"] = new_hash
            target["vertices"] = int(len(points))
            target["triangles"] = int(len(faces))
            target["bounds"] = bounds
            target["smoothing"] = "Taubin lambda|mu, volume preserving"
    if not hit:
        raise ValueError(f"scene.json 里找不到图层 {layer_id}")
    scene_path.write_text(json.dumps(scene, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


# ---------------------------------------------------------------- 主流程

def process(scene_dir: Path, layer_id: str, args):
    scene_path = scene_dir / "scene.json"
    if not scene_path.is_file():
        raise SystemExit(f"找不到 {scene_path}（请先用 start.sh run 生成 output）")
    scene = json.loads(scene_path.read_text(encoding="utf-8"))

    ids = [l["id"] for l in scene["layers"]]
    wanted = ids if layer_id == "all" else [layer_id]
    for name in wanted:
        if name not in ids:
            raise SystemExit(f"图层 {name} 不存在，可选：{', '.join(ids)}")

    print(f"{'图层':<12}{'顶点':>10}{'面':>10}"
          f"{'rms_lap 前':>13}{'rms_lap 后':>13}{'降低':>8}"
          f"{'体积变化':>11}{'最大位移':>10}")
    print("-" * 100)

    for name in wanted:
        layer = next(l for l in scene["layers"] if l["id"] == name)
        source = scene_dir / layer["file"]
        n_v, n_f = int(layer["vertices"]), int(layer["triangles"])
        points, faces = read_mesh(source, n_v, n_f)

        rms_before, vol_before = metrics(points, faces)

        # 冻结落在体数据边界上的顶点（它们不属于封闭曲面，平滑会把它们拉进来）
        lo, hi = points.min(axis=0), points.max(axis=0)
        span = np.maximum(hi - lo, 1e-12)
        eps = span * 1e-6
        on_box = np.zeros(len(points), dtype=bool)
        for k in range(3):
            on_box |= (points[:, k] <= lo[k] + eps[k]) | (points[:, k] >= hi[k] - eps[k])

        smoothed, n_stuck = taubin(points, faces, args.lam, args.mu, args.iters, freeze=on_box)

        rms_after, vol_after = metrics(smoothed, faces)
        move = np.linalg.norm(smoothed - points, axis=1)

        # 幂等命名：避免对已平滑过的图层再叠一层 .taubin 后缀
        stem = Path(layer["file"]).stem
        for tag in (".display", ".taubin"):
            stem = stem.replace(tag, "")
        target = scene_dir / (stem + args.suffix)
        write_mesh(target, smoothed, faces)

        drop = (1.0 - rms_after / rms_before) * 100.0 if rms_before > 0 else 0.0
        vol_pct = (vol_after - vol_before) / vol_before * 100.0 if vol_before > 0 else 0.0
        print(f"{name:<12}{n_v:>10}{n_f:>10}"
              f"{rms_before:>13.6f}{rms_after:>13.6f}{drop:>7.1f}%"
              f"{vol_pct:>10.3f}%{move.max():>10.4f}")

        # 失真守门：位移超过包围盒对角 5% 说明结果不可信，不能写进报告
        diagonal = float(np.linalg.norm(hi - lo))
        if diagonal > 0 and move.max() > 0.05 * diagonal:
            print(f"    ⚠ 最大位移 {move.max():.3f} mm 超过包围盒对角 {diagonal:.3f} mm 的 5%"
                  f"（冻结孤立顶点 {n_stuck} 个）—— 结果可能失真，勿直接用于报告")
        elif n_stuck:
            print(f"    （已冻结 {n_stuck} 个孤立顶点）")

        if args.patch:
            patch_scene(scene_path, name, target, smoothed, faces)
            print(f"    → 已写入 scene.json：{target.name}")

    print("-" * 100)
    print(f"迭代 {args.iters} 次，λ={args.lam}，μ={args.mu}")
    if args.patch:
        print("scene.json 已更新，刷新网页 /digimouse/ 即可看到平滑后的模型")
    else:
        print("未修改 scene.json（加 --patch 才会生效）")


def main():
    parser = argparse.ArgumentParser(
        description="KV260 MC 网格 Taubin 平滑（保体积）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--scene", type=Path, default=Path("../output/digimouse"),
                        help="包含 scene.json 的目录")
    parser.add_argument("--layer", default="body",
                        help="图层 id，或 all 表示全部")
    parser.add_argument("--iters", type=int, default=10,
                        help="迭代次数（每次含 λ 和 μ 各一遍）")
    parser.add_argument("--lam", type=float, default=0.50, help="正步系数 λ")
    parser.add_argument("--mu", type=float, default=-0.53, help="负步系数 μ（应为负数）")
    parser.add_argument("--suffix", default=".taubin.mesh", help="输出文件后缀")
    parser.add_argument("--patch", action="store_true", help="回写 scene.json（网页才看得到）")
    args = parser.parse_args()

    if args.mu >= 0:
        raise SystemExit("μ 必须为负数，否则会收缩。经典值 -0.53")
    if not (0 < args.lam < 1):
        raise SystemExit("λ 应在 (0,1)，经典值 0.5")

    process(args.scene, args.layer, args)


if __name__ == "__main__":
    sys.exit(main())
