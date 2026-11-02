#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
PORT="${PORT:-8870}"
profile="${PROFILE:-digimouse}"
mode="${1:-preview}"
case "$profile" in
    digimouse) buffer_mb="${BUFFER_MB:-32}"; engines="${ENGINES:-2}"; data_path="data/digimouse" ;;
    *) echo 'This release contains PROFILE=digimouse only.'; exit 2 ;;
esac
verify() { "$PYTHON" scripts/verify_bundle.py --scope "$1" --profile "$profile"; }
serve() {
    echo "Forward remote port $PORT in VS Code Ports; open its LOCAL address + /$profile/. Ctrl+C stops this viewer."
    cd "$1"
    exec "$PYTHON" -m http.server "$PORT" --bind 127.0.0.1
}
prepare_board() {
    "$PYTHON" scripts/check_environment.py
    verify runtime
    PYTHON="$PYTHON" bash scripts/install.sh
}
reconstruct() {
    "$PYTHON" scripts/reconstruct_scene.py --backend "$1" --data "$data_path" \
        --output "output/$profile" --buffer-mb "$buffer_mb" --engines "$engines"
}
case "$mode" in
    preview) verify preview; test -f "preview/$profile/scene.json"; serve preview ;;
    verify) verify all ;;
    check) verify runtime; "$PYTHON" scripts/check_environment.py ;;
    test) prepare_board; "$PYTHON" scripts/board_test.py ;;
    run)
        prepare_board
        "$PYTHON" scripts/board_test.py
        reconstruct pynq
        serve output
        ;;
    cpu)
        verify runtime; PYTHON="$PYTHON" bash scripts/install.sh
        reconstruct cpu
        serve output
        ;;
    *) echo 'Usage: bash start.sh [preview|verify|check|test|run|cpu]'; exit 2 ;;
esac
