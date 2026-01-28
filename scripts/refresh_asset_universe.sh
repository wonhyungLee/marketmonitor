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

# Normalize any FXCM_COPPER column in asset_universe backups
"$REPO/.venv/bin/python" scripts/normalize_asset_universe_csv.py

# Sync DB from asset_universe.csv (normalized)
"$REPO/.venv/bin/python" scripts/sync_db_from_asset_universe.py

# Recompute last 7 days and export CSV + Discord
"$REPO/.venv/bin/python" scripts/run_daily.py --window-days 7
