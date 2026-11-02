#!/usr/bin/env python3
"""
输入加载开销分解：把 volume.load() 的黑盒拆成「读取 / CRC32 / SHA256 / 全库路径」四段。

目的
----
KV260 端到端耗时里 input_s 占 69~93%（body 8.18s / 11.78s，heart 1.72s / 1.84s）。
但 input_s 是个黑盒计时，包含三件不同的事：

    1. 从磁盘/SD 卡读 122 MB          —— 不可避免，但可以和计算重叠
    2. 逐块累加 CRC32                 —— 完整性校验，一遍完整扫描
    3. 逐块累加 SHA256                —— 密码学校验，又一遍完整扫描（可与 CRC 同遍）
    4. memmap + 拷进 FPGA 缓冲         —— 格式转换，row_words 补齐

本脚本分别计时，给出每一段占 input_s 的比例。有了这个分解，才能判断
「增量校验 + 双缓冲预取」能把 input_s 压到多少，而不是拍脑袋说加速几倍。

用法
----
  /usr/local/share/pynq-venv/bin/python3 scripts/profile_io.py
  /usr/local/share/pynq-venv/bin/python3 scripts/profile_io.py --layers body skeleton heart
  /usr/local/share/pynq-venv/bin/python3 scripts/profile_io.py --repeat 3
"""

import argparse
import hashlib
import json
import struct
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "software"))

from pynq_mc import volume   # noqa: E402


def read_only(path: Path) -> int:
    """纯读：不做任何哈希。"""
    total = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            total += len(block)
    return total


def read_crc(path: Path) -> int:
    """读 + CRC32（跳过 128 字节头，和 volume.load 的行为一致）。"""
    crc = 0
    with path.open("rb") as stream:
        stream.seek(128)
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            crc = zlib.crc32(block, crc)
    return crc


def read_crc_sha(path: Path):
    """读 + CRC32 + SHA256 同遍完成。"""
    crc = 0
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        sha.update(stream.read(128))
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            crc = zlib.crc32(block, crc)
            sha.update(block)
    return crc, sha.hexdigest()


def best_of(fn, *args, repeat=2):
    """跑 repeat 次取最小值，减少缓存抖动干扰。"""
    best = None
    for _ in range(repeat):
        start = time.perf_counter()
        fn(*args)
        elapsed = time.perf_counter() - start
        if best is None or elapsed < best:
            best = elapsed
    return best


def main():
    parser = argparse.ArgumentParser(
        description="分解 volume.load 的读取 / CRC / SHA 开销",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--data", type=Path, default=ROOT / "data/digimouse")
    parser.add_argument("--layers", nargs="+",
                        default=["body", "skeleton", "heart", "eyes"])
    parser.add_argument("--repeat", type=int, default=2, help="每项重复次数，取最小")
    args = parser.parse_args()

    dataset_path = args.data / "dataset.json"
    if not dataset_path.is_file():
        raise SystemExit(f"找不到 {dataset_path}")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    by_id = {layer["id"]: layer for layer in dataset["layers"]}

    print(f"{'图层':<10}{'大小MB':>9}{'纯读':>9}{'读+CRC':>10}{'读+CRC+SHA':>12}"
          f"{'库load':>9}{'库load+sha':>11}{'SHA占':>8}{'CRC占':>8}")
    print("-" * 96)

    totals = {"load": 0.0, "load_sha": 0.0, "sha": 0.0, "crc": 0.0}

    for name in args.layers:
        if name not in by_id:
            print(f"{name:<10}  数据集里没有这个图层，跳过")
            continue
        meta = by_id[name]
        path = args.data / meta["input"]
        if not path.is_file():
            print(f"{name:<10}  文件不存在：{path}")
            continue
        expected = meta["sha256"]
        size_mb = path.stat().st_size / (1024 * 1024)

        t_read = best_of(read_only, path, repeat=args.repeat)
        t_crc = best_of(read_crc, path, repeat=args.repeat)
        t_sha = best_of(read_crc_sha, path, repeat=args.repeat)

        def lib_plain():
            return volume.load(path)

        def lib_sha():
            return volume.load(path, expected_sha256=expected)

        t_lib = best_of(lib_plain, repeat=args.repeat)
        t_lib_sha = best_of(lib_sha, repeat=args.repeat)

        crc_cost = max(t_crc - t_read, 0.0)
        sha_cost = max(t_sha - t_crc, 0.0)
        # 库路径新增的部分（memmap + 结构校验 + 尺寸检查）
        lib_overhead = max(t_lib_sha - t_sha, 0.0)

        sha_pct = (t_lib_sha - t_lib) / t_lib_sha * 100.0 if t_lib_sha > 0 else 0.0
        crc_pct = crc_cost / t_lib_sha * 100.0 if t_lib_sha > 0 else 0.0

        print(f"{name:<10}{size_mb:>9.1f}{t_read:>9.3f}{t_crc:>10.3f}{t_sha:>12.3f}"
              f"{t_lib:>9.3f}{t_lib_sha:>11.3f}{sha_pct:>7.1f}%{crc_pct:>7.1f}%")

        totals["load"] += t_lib
        totals["load_sha"] += t_lib_sha
        totals["sha"] += sha_pct
        totals["crc"] += crc_pct

    print("-" * 96)
    print(f"{'合计':<10}{'':>9}{'':>9}{'':>10}{'':>12}"
          f"{totals['load']:>9.3f}{totals['load_sha']:>11.3f}")
    n = len(args.layers)
    if n:
        print(f"\n平均：SHA 占 {totals['sha']/n:.1f}%，CRC 占 {totals['crc']/n:.1f}%")
    print("\n解读：")
    print("  纯读        —— 磁盘 I/O 的物理下限，只能靠「与计算重叠」隐藏，无法消除")
    print("  读+CRC      —— 加上 CRC32 的代价；差值 = CRC 成本")
    print("  读+CRC+SHA  —— 再加上 SHA256；差值 = SHA 成本")
    print("  库load+sha  —— volume.load 的完整路径（含 memmap、结构校验）")
    print("\n  若「SHA 占」显著（>20%），则「一次校验 + .verified 标记」能直接砍掉这部分；")
    print("  若「纯读」占比很高，则必须靠双缓冲预取把 I/O 藏到 FPGA 扫描背后。")


if __name__ == "__main__":
    sys.exit(main())
