#!/usr/bin/env bash
set -euo pipefail

# This script helps migrate an existing deployment from the legacy
# cycle/fear-euphoria CSV outputs to Forecast v1 (Crisis + Euphoria).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${ROOT_DIR}/data"

ts() { date -u +"%Y%m%dT%H%M%SZ"; }

STAMP="$(ts)"
BKP="${DATA_DIR}/backup_legacy_${STAMP}"
mkdir -p "${BKP}"

echo "[i] Backup dir: ${BKP}"

for f in \
  "cycles_monthly.csv" \
  "cycles_daily.csv" \
  "fear_euphoria_monthly.csv" \
  "fear_euphoria_daily.csv" \
  "fear_euphoria_calendar.csv"; do
  if [[ -f "${DATA_DIR}/${f}" ]]; then
    echo "[i] Moving ${f} -> ${BKP}/"
    mv "${DATA_DIR}/${f}" "${BKP}/"
  fi
done

echo "[i] Done. Next steps:"
echo "    1) Run the daily job:  python scripts/run_daily.py --force"
echo "    2) Rebuild site-react (if you deploy a static bundle): cd site-react && npm ci && npm run build"
echo "    3) Verify that ${DATA_DIR}/forecast_v1_daily.csv is generated."
