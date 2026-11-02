#!/usr/bin/env bash
# 判定 Vivado 器件数据是否完整 —— 复现「Cannot run timing on a non-timing device」的决定性测试。
#
# 用法：
#   bash check_vivado_device_data.sh                 # 默认 2022.2 + xck26-sfvc784-2LV-c
#   bash check_vivado_device_data.sh 2023.2 xck26-sfvc784-2LV-c
#
# 退出码：0 = 器件数据可用；1 = 器件数据不完整；2 = 环境/工具问题
set -u

VIVADO_VER="${1:-2022.2}"
PART="${2:-xck26-sfvc784-2LV-c}"
VIVADO_ROOT="${HOME}/Xilinx/Vivado/${VIVADO_VER}"

echo "=========================================================="
echo " Vivado 器件数据完整性检查"
echo " 版本: ${VIVADO_VER}   器件: ${PART}"
echo "=========================================================="

if [ ! -x "${VIVADO_ROOT}/bin/vivado" ]; then
  echo "❌ 找不到 ${VIVADO_ROOT}/bin/vivado"
  echo "   请先 source ${VIVADO_ROOT}/settings64.sh，或确认安装路径。"
  exit 2
fi

# ---- 1. 器件目录规模 -------------------------------------------------------
PARTS="${VIVADO_ROOT}/data/parts/xilinx/zynquplus"
if [ -d "$PARTS" ]; then
  SIZE_MB=$(du -sm "$PARTS" 2>/dev/null | awk '{print $1}')
  echo ""
  echo "[1] zynquplus 器件目录: ${SIZE_MB} MB"
  COUNT=$(ls "${PARTS}/devint/zynquplus" 2>/dev/null | grep -c '^xczu')
  echo "    xczu* 芯片目录数: ${COUNT}"
  if [ "${SIZE_MB}" -lt 3000 ] || [ "${COUNT}" -lt 5 ]; then
    echo "    ⚠ 规模偏小（正常应 ≈7.6 GB、含十余个 xczu*），器件数据很可能不完整。"
  else
    echo "    ✅ 规模正常。"
  fi
else
  echo "[1] ❌ 目录不存在: $PARTS"
fi

# ---- 2. 决定性测试：空工程时序分析 -----------------------------------------
echo ""
echo "[2] 决定性测试：空工程 link_design + report_timing_summary"
TCL=$(mktemp /tmp/dsh_dev_XXXX.tcl)
LOG=$(mktemp /tmp/dsh_dev_XXXX.log)
RPT=/tmp/dsh_device_timing.rpt
rm -f "$RPT"
cat > "$TCL" <<EOF
link_design -part ${PART}
report_timing_summary -file ${RPT}
exit
EOF

# shellcheck disable=SC1090
source "${VIVADO_ROOT}/settings64.sh" >/dev/null 2>&1
vivado -mode batch -source "$TCL" > "$LOG" 2>&1
RC=$?

BAD=0
grep -q "Clock region file  is not available" "$LOG" && { echo "    ❌ 出现 'Clock region file  is not available'（文件名为空）"; BAD=1; }
grep -q "Failed to load clock programmable delays" "$LOG" && { echo "    ❌ 出现 'Failed to load clock programmable delays'"; BAD=1; }
grep -q "Cannot run timing on a non-timing device" "$LOG" && { echo "    ❌ 出现 'Cannot run timing on a non-timing device'"; BAD=1; }

if [ -f "$RPT" ]; then
  echo "    ✅ 生成时序报告: $RPT ($(stat -c%s "$RPT") 字节)"
else
  echo "    ❌ 未生成时序报告"
  BAD=1
fi

if [ "$BAD" -eq 0 ] && [ "$RC" -eq 0 ]; then
  echo ""
  echo "结论: ✅ 器件数据可用，可以生成 bitstream。"
  echo "      若实现阶段仍失败，请转向检查 Block Design（m_axi 是否悬空、时钟是否连接）。"
  rm -f "$TCL" "$LOG"
  exit 0
fi

echo ""
echo "结论: ❌ 器件数据不完整。修复方式："
echo "      Vivado → Help → Add Design Tools or Devices"
echo "        · 只勾 Devices for Custom Platforms → UltraScale+"
echo "        · Engineering Sample Devices 不勾"
echo "        · Download Size 应降到几个 GB（不是 60 GB）"
echo "      父项复选框会全选子项，需再逐个点掉 SoCs / 7 Series / UltraScale / Versal ACAP。"
echo ""
echo "      完整日志: $LOG"
exit 1
