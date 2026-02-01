#!/usr/bin/env bash
set -euo pipefail

# Start the watcher + FastAPI server.
#
# Defaults assume a virtualenv in `.venv/`.
# You can override:
#   PROJECT_ROOT=/path/to/repo
#   PYTHON=/path/to/python
#   HOST=0.0.0.0 PORT=8000

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-$PROJECT_ROOT/.venv/bin/python3}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

cd "$PROJECT_ROOT"

# Kill existing processes (best effort)
pkill -f "uvicorn app.main:app" >/dev/null 2>&1 || true
pkill -f "scripts/watcher.py" >/dev/null 2>&1 || true

# Start Watcher
nohup "$PYTHON" scripts/watcher.py > watcher.log 2>&1 &
echo "Watcher started (PID $!)"

# Start Server
export PYTHONUNBUFFERED=1
nohup "$PYTHON" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --proxy-headers > server.log 2>&1 &
echo "Server started (PID $!)"
