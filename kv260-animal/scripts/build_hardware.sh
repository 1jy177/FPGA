#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${MC_KV260_BUILD:?Set MC_KV260_BUILD to a large-storage build directory}"
if [[ -e "$MC_KV260_BUILD/vivado/mc_kv260.xpr" ]]; then
    echo 'Use a fresh build directory; existing completed hardware is preserved.'; exit 2
fi
mkdir -p "$MC_KV260_BUILD"
vitis_hls -f "$ROOT/hardware/hls/run.tcl" -tclargs all 2>&1 | tee "$MC_KV260_BUILD/hls.log"
vivado -mode batch -source "$ROOT/hardware/vivado/build.tcl" -log "$MC_KV260_BUILD/vivado.log" -journal "$MC_KV260_BUILD/vivado.jou"
python3 "$ROOT/scripts/manifest.py"
# 报告收集步骤已移除（报告不再随包分发）
