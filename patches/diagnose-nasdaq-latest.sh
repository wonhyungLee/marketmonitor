#!/usr/bin/env bash
set -euo pipefail

DB=/home/ubuntu/시장감지모델/warroom.db

if [ ! -f "$DB" ]; then
  echo "DB not found: $DB" >&2
  exit 1
fi

echo "== env =="
sudo grep USE_RECEIVED_AT_FOR_LATEST /etc/warroom.env || true

echo "\n== latest by received_at =="
python3 - <<'PY'
import sqlite3, datetime
conn = sqlite3.connect('/home/ubuntu/시장감지모델/warroom.db')
rows = conn.execute("""
  select value, time_utc_ms, received_at
  from market_observations
  where series_id='NASDAQ_DLY_IXIC'
  order by received_at desc
  limit 5
""").fetchall()
for v, ms, r in rows:
    dt = datetime.datetime.utcfromtimestamp(ms/1000).isoformat()+"Z"
    print(f"value={v} time_utc={dt} received_at={r}")
PY

echo "\n== latest by time_utc =="
python3 - <<'PY'
import sqlite3, datetime
conn = sqlite3.connect('/home/ubuntu/시장감지모델/warroom.db')
rows = conn.execute("""
  select value, time_utc_ms, received_at
  from market_observations
  where series_id='NASDAQ_DLY_IXIC'
  order by time_utc_ms desc
  limit 5
""").fetchall()
for v, ms, r in rows:
    dt = datetime.datetime.utcfromtimestamp(ms/1000).isoformat()+"Z"
    print(f"value={v} time_utc={dt} received_at={r}")
PY

echo "\n== last webhook trigger =="
sudo journalctl -u warroom -b --no-pager | grep "trigger=webhook" | tail -n 3 || true

echo "\n== last discord send =="
sudo journalctl -u warroom -b --no-pager | grep "discord webhook sent" | tail -n 3 || true
