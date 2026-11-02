#!/usr/bin/env python3
"""
把两个 .mesh 用平面着色渲染成并排对比图（numpy + Pillow，无需 GPU / WebGL）。

为什么要这个工具
----------------
WebGL 查看器使用逐顶点平均法线（平滑着色），9° 的二面角在屏幕上几乎看不出来，
因此"人眼看不出差别"并不等于"几何没变"。要判断平滑是否可见，必须用**平面着色**
（每个三角面一个法线），这样面片折角会直接变成明暗台阶。

实现要点
--------
- 正交投影 + painter's algorithm（按面心深度从远到近填充），无需 z-buffer
- 用 Pillow 的 ImageDraw.polygon 填充，C 速度，十几万面可在数秒内完成
- 光照：环境项 + 单向平行光，纯灰度，便于比较明暗台阶

用法
----
  python render_compare.py --a body.display.mesh --b body.taubin.mesh --out cmp.png
  python render_compare.py --a X --b Y --out cmp.png --zoom 0.35 0.30 0.55 0.45
"""

import argparse
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def load_mesh(path: Path):
    """无头部网格：float32 xyz，随后 uint32 ijk。用 scene 里的顶点/面数反推。"""
    size = path.stat().st_size
    # 顶点数未知，但 N*12 + M*12 = size；需要外部给定。这里用常见的 count 文件回退：
    counts = path.with_suffix(path.suffix + ".counts")
    if counts.is_file():
        nv, nf = [int(x) for x in counts.read_text().split()[:2]]
    else:
        raise SystemExit(f"缺少 {counts}（需要两行：顶点数 面数）")
    p = np.fromfile(path, dtype="<f4", count=nv * 3).reshape(nv, 3).astype(np.float64)
    f = np.fromfile(path, dtype="<u4", count=nf * 3, offset=nv * 12).reshape(nf, 3)
    return p, f


def rotation(yaw: float, pitch: float):
    cy, sy = np.cos(yaw), np.sin(yaw)
    cp, sp = np.cos(pitch), np.sin(pitch)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    return Rx @ Ry


def render(points, faces, size=760, yaw=0.55, pitch=0.12,
           light=(-0.45, 0.55, 0.70), bg=(18, 22, 30),
           ambient=0.22, diffuse=0.78):
    """返回 PIL.Image（RGB）。平面着色。"""
    p = points - (points.min(0) + points.max(0)) / 2.0
    scale = 1.0 / np.max(np.abs(p)) if np.max(np.abs(p)) > 0 else 1.0
    p = p * scale * 0.92
    p = p @ rotation(yaw, pitch).T

    # 正交投影到 [-1,1]，y 轴翻转到图像坐标
    half = size / 2.0
    xy = np.empty((len(p), 2))
    xy[:, 0] = p[:, 0] * half + half
    xy[:, 1] = -p[:, 1] * half + half

    v0, v1, v2 = p[faces[:, 0]], p[faces[:, 1]], p[faces[:, 2]]
    n = np.cross(v1 - v0, v2 - v0)
    ln = np.linalg.norm(n, axis=1)
    ln[ln == 0] = 1.0
    n = n / ln[:, None]
    L = np.asarray(light, dtype=np.float64)
    L = L / np.linalg.norm(L)
    # 背面法线翻正，避免内部面全黑
    flip = n[:, 2] < 0
    n[flip] *= -1.0
    shade = ambient + diffuse * np.clip(n @ L, 0.0, 1.0)
    shade = np.clip(shade, 0.0, 1.0)

    depth = (v0[:, 2] + v1[:, 2] + v2[:, 2]) / 3.0
    order = np.argsort(depth)          # 远（z 小）先画

    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)

    # 为速度：把颜色量化成 32 级，按级别分组批量画
    levels = np.clip((shade * 31).astype(np.int32), 0, 31)
    base = np.array(bg, dtype=np.float64)
    palette = {}
    for lv in range(32):
        v = 16 + int(255 * (lv / 31.0) * 0.92)
        palette[lv] = (min(v, 255), min(v, 255), min(int(v * 0.98), 255))

    xy_l = xy.tolist()
    faces_l = faces.tolist()
    levels_l = levels.tolist()
    for i in order.tolist():
        a, b, c = faces_l[i]
        draw.polygon([tuple(xy_l[a]), tuple(xy_l[b]), tuple(xy_l[c])],
                     fill=palette[levels_l[i]])
    return img


def crop_zoom(img, box):
    """box = (x0,y0,frac_w,frac_h) 归一化，返回放大的裁剪。"""
    w, h = img.size
    x0, y0, fw, fh = box
    left, top = int(x0 * w), int(y0 * h)
    right, bottom = left + int(fw * w), top + int(fh * h)
    return img.crop((left, top, right, bottom))


def label(img, text, height=34):
    out = Image.new("RGB", (img.width, img.height + height), (0, 0, 0))
    out.paste(img, (0, height))
    d = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 22)
    except Exception:
        font = ImageFont.load_default()
    d.text((10, 6), text, fill=(235, 240, 250), font=font)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=Path, required=True)
    ap.add_argument("--b", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--size", type=int, default=760)
    ap.add_argument("--yaw", type=float, default=0.55)
    ap.add_argument("--pitch", type=float, default=0.12)
    ap.add_argument("--zoom", nargs=4, type=float, default=None,
                    help="x0 y0 frac_w frac_h（归一化），额外输出放大裁剪")
    ap.add_argument("--labels", nargs=2, default=["平滑前", "平滑后"])
    args = ap.parse_args()

    pa, fa = load_mesh(args.a)
    pb, fb = load_mesh(args.b)
    print(f"A: {len(pa)} 顶点 {len(fa)} 面")
    print(f"B: {len(pb)} 顶点 {len(fb)} 面")

    ia = render(pa, fa, args.size, args.yaw, args.pitch)
    ib = render(pb, fb, args.size, args.yaw, args.pitch)

    rows = [np.hstack([np.array(label(ia, f"A  {args.labels[0]}")),
                       np.array(label(ib, f"B  {args.labels[1]}"))])]
    if args.zoom:
        za = crop_zoom(ia, args.zoom).resize((args.size, args.size), Image.NEAREST)
        zb = crop_zoom(ib, args.zoom).resize((args.size, args.size), Image.NEAREST)
        rows.append(np.hstack([np.array(label(za, f"A 放大 · {args.labels[0]}")),
                               np.array(label(zb, f"B 放大 · {args.labels[1]}"))]))

    out = Image.fromarray(np.vstack(rows))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.save(args.out)
    print(f"已写出 {args.out}  ({out.width}x{out.height})")


if __name__ == "__main__":
    main()
