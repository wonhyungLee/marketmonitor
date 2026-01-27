#!/bin/bash
PROJECT_DIR="/home/dldnjsrk/remote-ubuntu/시장감지모델"
cd "$PROJECT_DIR" || exit 1

# Kill existing processes
pkill -f "uvicorn app.main:app"
pkill -f "scripts/watcher.py"

# Start Watcher
nohup .venv/bin/python3 scripts/watcher.py > watcher.log 2>&1 &
echo "Watcher started (PID $!)"

# Start Server
export PYTHONUNBUFFERED=1
nohup .venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > server_final.log 2>&1 &
echo "Server started (PID $!)"
