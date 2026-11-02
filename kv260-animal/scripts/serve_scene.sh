#!/usr/bin/env bash
# 启动「静态查看器 + 阈值实时重建」服务。
#
# 为什么必须 root：PYNQ 的 MMAP 需要访问 /dev/mem，普通用户会报
# "Root permissions required."。这里用 sudo -n（免密）启动，日志留在 /tmp。
#
# 用法：
#   bash scripts/serve_scene.sh            # 默认 8870 端口
#   bash scripts/serve_scene.sh 9000       # 指定端口
#   bash scripts/serve_scene.sh stop       # 停止
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-8870}"
PY=/usr/local/share/pynq-venv/bin/python3
LOG=/tmp/scene_server.log

# ---- 停止：把所有可能占用端口的服务都停掉 ----
stop_all() {
  sudo -n pkill -f "scene_server.py"  2>/dev/null || true
  sudo -n pkill -f "http.server"      2>/dev/null || true
  sudo -n pkill -f "http\.server"     2>/dev/null || true
}

if [ "$PORT" = "stop" ]; then
  stop_all
  echo "已停止"
  exit 0
fi

if [ ! -x "$PY" ]; then
  echo "找不到 Python：$PY"; exit 1
fi

# ---- 启动前清场：静态服务也会占着同一个端口 ----
stop_all
for _ in $(seq 1 10); do
  ss -ltn 2>/dev/null | grep -q ":$PORT " || break
  sleep 1
done
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "端口 $PORT 仍被占用，请先手动停止："
  ss -ltnp 2>/dev/null | grep ":$PORT " || true
  echo "提示：sudo pkill -f 'http.server' 或换端口 bash scripts/serve_scene.sh 8871"
  exit 1
fi

cd "$ROOT" || exit 1
sudo -n env XILINX_XRT=/usr nohup "$PY" scripts/scene_server.py --port "$PORT" >"$LOG" 2>&1 &

# 等 overlay 就绪（首次约 5 秒），最多等 60 秒
for _ in $(seq 1 30); do
  grep -q "overlay" "$LOG" 2>/dev/null && break
  sleep 2
done

if grep -q "overlay 已就绪" "$LOG" 2>/dev/null; then
  IP=$(hostname -I | awk '{print $1}')
  echo "FPGA overlay 就绪"
  echo "  平滑后：http://$IP:$PORT/digimouse/"
  echo "  平滑前：http://$IP:$PORT/compare_before/"
  echo "  日志：  $LOG"
else
  echo "启动异常，日志如下："
  tail -20 "$LOG"
  exit 1
fi