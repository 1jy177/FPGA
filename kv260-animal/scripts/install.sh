#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARED="$ROOT/shared"
test -d "$SHARED"
"${CXX:-g++}" -std=c++14 -O2 -ffp-contract=off -fno-fast-math -D_FILE_OFFSET_BITS=64 -fPIC -shared "$ROOT/software/native/mesh.cpp" \
  -I"$SHARED/common" -I"$SHARED/kernel/src" -o "$ROOT/software/pynq_mc/libmc_mesh.so"
"${PYTHON:-python3}" -c 'import numpy; print("NumPy", numpy.__version__)'
echo "Native library built for $(uname -m)."
echo "Run from this directory: export PYTHONPATH=\"$ROOT/software\""
