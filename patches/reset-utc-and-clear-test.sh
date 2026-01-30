#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=/etc/warroom.env
DB_FILE=/home/ubuntu/시장감지모델/warroom.db

if [ ! -f "$ENV_FILE" ]; then
  echo "[ERROR] $ENV_FILE not found" >&2
  exit 1
fi

if [ ! -f "$DB_FILE" ]; then
  echo "[ERROR] $DB_FILE not found" >&2
  exit 1
fi

python3 - <<'PY'
from pathlib import Path

path = Path('/etc/warroom.env')
text = path.read_text() if path.exists() else ''
lines = text.splitlines()
key = 'USE_RECEIVED_AT_FOR_LATEST'

new_lines = []
seen = False
for line in lines:
    if not line.strip() or line.strip().startswith('#'):
        new_lines.append(line)
        continue
    k = line.split('=', 1)[0].strip()
    if k == key:
        new_lines.append(f"{key}=false")
        seen = True
    else:
        new_lines.append(line)

if not seen:
    new_lines.append(f"{key}=false")

path.write_text("\n".join(new_lines) + "\n")
PY

python3 - <<'PY'
import sqlite3, datetime
conn = sqlite3.connect('/home/ubuntu/시장감지모델/warroom.db')
cur = conn.execute(
    "delete from market_observations where series_id='NASDAQ_DLY_IXIC' and value=12345.0"
)
conn.commit()
print(f"deleted {cur.rowcount} rows for 12345 test data")

rows = conn.execute("""
  select value, time_utc_ms, received_at
  from market_observations
  where series_id='NASDAQ_DLY_IXIC'
    and time_utc_ms > strftime('%s','now')*1000
  order by time_utc_ms desc
  limit 5
""").fetchall()
if rows:
    print("future-dated NASDAQ records (time_utc > now):")
    for v, ms, r in rows:
        dt = datetime.datetime.utcfromtimestamp(ms/1000).isoformat()+"Z"
        print(f"value={v} time_utc={dt} received_at={r}")
PY

sudo systemctl restart warroom
systemctl status warroom --no-pager

echo "OK"
