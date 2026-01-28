#!/usr/bin/env bash
set -euo pipefail
REPO="/home/dldnjsrk/remote-ubuntu/시장감지모델"
SECRET_FILE="/home/dldnjsrk/remote-ubuntu/개인정보.txt"
cd "$REPO"
export PYTHONPATH="$REPO"
: "${WEBHOOK_TOKEN:=local}"
export WEBHOOK_TOKEN
if [[ -f "$SECRET_FILE" ]]; then
  DISCORD_WEBHOOK_URL=$(python3 - <<'PY'
import re
from pathlib import Path
p=Path('/home/dldnjsrk/remote-ubuntu/개인정보.txt')
text=p.read_text(encoding='utf-8', errors='ignore')
m=re.search(r'https?://\S+', text)
print(m.group(0) if m else '')
PY
  )
  if [[ -n "$DISCORD_WEBHOOK_URL" ]]; then
    export DISCORD_WEBHOOK_URL
  fi
fi
"$REPO/.venv/bin/python" scripts/run_daily.py --window-days 7
