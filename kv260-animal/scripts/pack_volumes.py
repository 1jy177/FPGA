#!/usr/bin/env python3
"""
把 VXL4 体数据打包成 VXL4Z（zlib 压缩），并验证完整性等价。

为什么
------
KV260 实测：body 端到端 11.783 s，其中 input（读取+校验体数据）8.178 s = 69.4%；
skeleton 75.2%；heart 93.2%。FPGA 扫描只占 5~40%。
而医学体数据极度冗余 —— body 有 98.8%、skeleton 有 99.8% 的体素是同一个值。
因此把「读 120 MB」换成「读 8 MB + 解压」，是最直接的一刀。

安全性
------
压缩文件复用原始 128 字节头，其中 CRC32 与 payload 长度都指**解压后**的数据；
expected_sha256 依然是对 header+payload 的 SHA256。所以：
  - dataset.json 里的 sha256 一个字节都不用改
  - 原有的完整性校验强度完全不降低
  - 解压后再算 CRC32/SHA256，任何一位错误都会被抓住

用法
----
  # 压缩 data/digimouse 下全部体数据（推荐 level 1，速度快）
  /usr/local/share/pynq-venv/bin/python3 scripts/pack_volumes.py

  # 指定目录 / 压缩级别 / 只压某几个
  ... pack_volumes.py --data data/digimouse --level 6 --only body skeleton

  # 压缩后立即逐字节回读验证（默认开启，--no-verify 关闭）
"""

import argparse
import json
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "software"))

from pynq_mc import volume   # noqa: E402


def human(n_bytes: float) -> str:
    return f"{n_bytes / (1024 * 1024):.1f}"


def main():
    parser = argparse.ArgumentParser(
        description="VXL4 -> VXL4Z 压缩打包",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--data", type=Path, default=ROOT / "data/digimouse",
                        help="包含 .vxl4.bin 与 dataset.json 的目录")
    parser.add_argument("--level", type=int, default=1,
                        help="zlib 压缩级别 1..9（1 最快，9 最小）")
    parser.add_argument("--only", nargs="+", default=None,
                        help="只处理这些图层 id")
    parser.add_argument("--no-verify", action="store_true",
                        help="跳过压缩后的回读验证")
    args = parser.parse_args()

    dataset_path = args.data / "dataset.json"
    expected_by_input = {}
    if dataset_path.is_file():
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        for layer in dataset["layers"]:
            expected_by_input[layer["input"]] = (layer["id"], layer["sha256"])

    sources = sorted(args.data.glob("*.vxl4.bin"))
    if not sources:
        raise SystemExit(f"{args.data} 下没有 .vxl4.bin")

    if args.only:
        wanted = set(args.only)
        sources = [p for p in sources
                   if expected_by_input.get(p.name, (None,))[0] in wanted]
        if not sources:
            raise SystemExit(f"--only {args.only} 没有匹配到任何文件")

    print(f"压缩级别 {args.level}，共 {len(sources)} 个文件\n")
    header_line = (f"{'文件':<22}{'原始MB':>9}{'压缩MB':>9}{'压缩比':>8}"
                   f"{'耗时s':>8}{'校验':>7}")
    print(header_line)
    print("-" * len(header_line))

    total_raw = 0
    total_packed = 0
    total_time = 0.0
    failures = []

    for source in sources:
        layer_id, expected_sha = expected_by_input.get(source.name, (source.stem, None))
        start = time.perf_counter()
        try:
            header, payload = volume.load_payload(source)
            raw_len = len(payload)
            packed_len = volume.write_compressed(
                source.with_suffix("").with_suffix(volume.VXLZ_EXT),
                header, payload, level=args.level)
        except Exception as exc:                      # noqa: BLE001
            print(f"{source.name:<22}{'--':>9}{'--':>9}{'--':>8}{'--':>8}  失败: {exc}")
            failures.append((source.name, str(exc)))
            continue
        elapsed = time.perf_counter() - start

        status = "跳过"
        if not args.no_verify:
            try:
                # 用真正的加载路径回读，连同 CRC32 与 SHA256 一起验证
                _hdr, voxels = volume.load(source, expected_sha256=expected_sha,
                                           prefer_compressed=True)
                expect_elems = raw_len // 2
                if voxels.size != expect_elems:
                    raise ValueError(f"元素数不符 {voxels.size} != {expect_elems}")
                status = "PASS"
            except Exception as exc:                  # noqa: BLE001
                status = "FAIL"
                failures.append((source.name, str(exc)))

        ratio = raw_len / packed_len if packed_len else 0.0
        print(f"{source.name:<22}{human(raw_len):>9}{human(packed_len):>9}"
              f"{ratio:>7.2f}x{elapsed:>8.2f}{status:>7}")

        total_raw += raw_len
        total_packed += packed_len
        total_time += elapsed

    print("-" * len(header_line))
    ratio_all = total_raw / total_packed if total_packed else 0.0
    print(f"{'合计':<22}{human(total_raw):>9}{human(total_packed):>9}"
          f"{ratio_all:>7.2f}x{total_time:>8.2f}")
    print(f"\n磁盘节省：{human(total_raw - total_packed)} MB"
          f"（{100.0 * (1 - total_packed / total_raw):.1f}%）")

    if failures:
        print(f"\n有 {len(failures)} 个文件出错：")
        for name, reason in failures:
            print(f"  {name}: {reason}")
        return 1

    print("\n提示：")
    print("  1. 原 .vxl4.bin 不要删 —— 打包脚本和文档仍需要它做溯源；")
    print("     运行时 volume.load() 会自动优先读同名 .vxl4z。")
    print("  2. 现在重跑重建即可看到加载时间下降：")
    print("     sudo -E env XILINX_XRT=/usr PYTHON=/usr/local/share/pynq-venv/bin/python3 \\")
    print("       bash start.sh run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
