#!/usr/bin/env bash
set -euo pipefail

sudo bash -c 'cat > /etc/systemd/system/warroom.service <<EOF
[Unit]
Description=WarRoom FastAPI ingest
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/시장감지모델
EnvironmentFile=/etc/warroom.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/시장감지모델/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF'

sudo systemctl daemon-reload
sudo systemctl restart warroom
systemctl status warroom --no-pager
