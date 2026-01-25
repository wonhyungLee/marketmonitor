#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/dldnjsrk/remote-ubuntu/시장감지모델"

cd "$ROOT"
export PYTHONPATH="$ROOT"

"$ROOT/.venv/bin/python" "$ROOT/scripts/run_daily.py"
