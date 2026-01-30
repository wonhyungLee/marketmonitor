#!/usr/bin/env bash
set -euo pipefail

INFO_FILE="${1:-/home/ubuntu/시장감지모델/개인정보.txt}"
ENV_FILE="/etc/warroom.env"
APP_DIR="/home/ubuntu/시장감지모델"
PYTHON_BIN="$APP_DIR/.venv/bin/python"

if [ ! -f "$INFO_FILE" ]; then
  echo "[ERROR] 개인정보 파일을 찾을 수 없습니다: $INFO_FILE" >&2
  echo "        경로를 인자로 넘겨 실행하세요." >&2
  echo "        예) sudo bash $0 /path/to/개인정보.txt" >&2
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "[ERROR] python 실행 파일이 없습니다: $PYTHON_BIN" >&2
  exit 1
fi

DISCORD_URL="$($PYTHON_BIN - "$INFO_FILE" <<'PY'
from pathlib import Path
import re
import sys

p = Path(sys.argv[1])
text = p.read_text(errors="ignore")

# Discord webhook URL pattern
pattern = re.compile(r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\S+")
match = pattern.search(text)
if not match:
    print("", end="")
    sys.exit(1)
url = match.group(0)
# strip trailing punctuation if present
url = url.rstrip(')"\'<>.,;')
print(url)
PY
)"

if [ -z "$DISCORD_URL" ]; then
  echo "[ERROR] 개인정보.txt에서 DISCORD_WEBHOOK_URL을 찾지 못했습니다." >&2
  exit 1
fi

sudo "$PYTHON_BIN" - "$ENV_FILE" "$DISCORD_URL" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
discord_url = sys.argv[2]
text = path.read_text() if path.exists() else ''

updates = {
    'DISCORD_WEBHOOK_URL': discord_url,
    'DISCORD_ENABLED': 'true',
    'DISCORD_TIMEOUT_SEC': '8',
    'DISCORD_RETRY_MAX': '3',
    'AUTO_REFRESH_DAILY': 'false',
}

lines = text.splitlines()
seen = set()
new_lines = []
for line in lines:
    if not line.strip() or line.strip().startswith('#'):
        new_lines.append(line)
        continue
    key = line.split('=', 1)[0].strip()
    if key in updates:
        new_lines.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        new_lines.append(line)

for key, val in updates.items():
    if key not in seen:
        new_lines.append(f"{key}={val}")

path.write_text("\n".join(new_lines) + "\n")
PY

sudo bash -c "cat > /etc/systemd/system/warroom-daily.service <<'EOF'
[Unit]
Description=WarRoom scheduled daily job
After=network.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/시장감지모델
EnvironmentFile=/etc/warroom.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/시장감지모델/.venv/bin/python /home/ubuntu/시장감지모델/scripts/run_scheduled_job.py
EOF"

sudo bash -c "cat > /etc/systemd/system/warroom-daily.timer <<'EOF'
[Unit]
Description=WarRoom scheduled daily job timer (every 3 hours starting 10:00)

[Timer]
OnCalendar=*-*-* 01,04,07,10,13,16,19,22:00:00
Persistent=true
Unit=warroom-daily.service

[Install]
WantedBy=timers.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable --now warroom-daily.timer
sudo systemctl restart warroom

systemctl status warroom --no-pager
systemctl list-timers --no-pager | grep warroom-daily || true

echo "OK"
