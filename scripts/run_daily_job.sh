#!/usr/bin/env bash
set -euo pipefail

# Portable runner.
# You can override PROJECT_ROOT or PYTHON if needed.
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-$PROJECT_ROOT/.venv/bin/python}"

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT"

"$PYTHON" "$PROJECT_ROOT/scripts/run_daily.py"
