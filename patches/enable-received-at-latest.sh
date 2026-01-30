#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=/etc/warroom.env

if [ ! -f "$ENV_FILE" ]; then
  echo "[ERROR] $ENV_FILE not found" >&2
  exit 1
fi

python3 - <<'PY'
from pathlib import Path

path = Path('/etc/warroom.env')
text = path.read_text() if path.exists() else ''
lines = text.splitlines()
key = 'USE_RECEIVED_AT_FOR_LATEST'
value = 'true'

new_lines = []
seen = False
for line in lines:
    if not line.strip() or line.strip().startswith('#'):
        new_lines.append(line)
        continue
    k = line.split('=', 1)[0].strip()
    if k == key:
        new_lines.append(f"{key}={value}")
        seen = True
    else:
        new_lines.append(line)

if not seen:
    new_lines.append(f"{key}={value}")

path.write_text("\n".join(new_lines) + "\n")
PY

sudo systemctl restart warroom
systemctl status warroom --no-pager

# Timer uses same env; no restart needed unless you want immediate run.

echo "OK"
