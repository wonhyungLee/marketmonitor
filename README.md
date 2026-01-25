# MarketMonitor (WarRoom)

Webhook ingest + daily regime engine (WARMUP/NORMAL/DEFCON2/DEFCON1) + static site for charts/tables.

## Generate Daily States (From CSV)

Uses `지표데이터/NASDAQ_DLY_IXIC, 1D.csv` + `1W.csv` + `1M.csv` to compute daily states and writes:
- `data/market_states_daily.csv`
- `data/market_states_daily.jsonl`
- `data/market_states.sqlite3`

```powershell
python scripts/backtest_daily_states.py
```

## Generate Portfolio Recommendations (From DB or 투자자산모음.csv)

Uses `warroom.db` (TradingView webhooks) if available, otherwise `지표데이터/투자자산모음.csv` (auto-detected) + `data/market_states_daily.csv` and writes:
- `data/portfolio_daily.csv`

Notes:
- If the file contains US Treasury yields (`US10Y`, `US02Y`), the script will synthesize bond return indices (`UST10Y`, `UST2Y`) and include them as allocatable assets.
- Use DB explicitly via `--use-db --db-path warroom.db`.
- To backfill asset CSV into DB: `python scripts/backfill_from_csv.py --data-dir 지표데이터`.
- Webhook ingest triggers portfolio refresh when `AUTO_REFRESH_PORTFOLIO=true` (throttled by `AUTO_REFRESH_MIN_INTERVAL_SEC`).

```powershell
python scripts/build_portfolio_daily.py
```

## View As A Site (Table + Charts)

```powershell
python -m http.server 8000
```

Open: `http://localhost:8000/site/`

## Scoring / Thresholds (v2.0)

Weights:
- T10Y2Y cross-up: +2.0
- BAML HY spread risk: +1.5
- WEI recession trend: +1.0
- Copper/Gold under MA200 (5D): +0.5
- UMCSENT < 65: +0.5
- SAHMREALTIME >= 0.50: hard DEFCON1

State transitions (hysteresis):
- NORMAL -> DEFCON2: score >= 2.0 for 3 days
- DEFCON2 -> DEFCON1: score >= 3.5 for 3 days (or hard trigger)
- DEFCON2 -> NORMAL: score < 2.0 for 5 days
- DEFCON1 -> DEFCON2: score <= 3.0 for 10 days

## Hybrid Model (MA200 + Dynamic Allocation)

Macro state stays the same (NORMAL/DEFCON2/DEFCON1), but the recommended action/weight can use NASDAQ MA200 as a trend signal.

Default (`ALLOCATION_MODEL=trend_vol_target`, long-only, leverage capped):
- Trend=DOWN: `DEFENSIVE / CASH` (equity weight = 0)
- Trend=UP: `equity_weight = min(LEVERAGE_CAP, TARGET_VOL_ANN / VOL_ANN(VOL_WINDOW_DAYS))`
  - NORMAL: `BUY / ACCUMULATE`
  - DEFCON2/DEFCON1: `CAUTIOUS HOLD` (can be reduced via `MACRO_MULTIPLIER_*`)

Set `ALLOCATION_MODEL=fixed` to use the legacy `EQUITY_WEIGHT_*` table.

The daily engine persists `trend` + `allocation` into `reasons_json`, and Discord reports include `Trend` + `Equity Weight`.

## Data Quality / Stale Safety

- Health tracks both `late` (past expected publication lag) and `stale` (too old to score).
- When already in a DEFCON regime and critical sensors are `stale`, the engine can hold the last score to avoid false NORMAL due to missing data (`HOLD_LAST_SCORE_ON_DEFCON_STALE=true`).

## Config (Env Vars)

See `.env.example` for defaults. Key knobs:
- Thresholds: `DEFCON2_SCORE_THRESHOLD`, `DEFCON1_SCORE_THRESHOLD`, `DEFCON1_EXIT_SCORE_THRESHOLD`
- Weights: `WEIGHT_*`, `SAHM_HARD_TRIGGER_THRESHOLD`
- Trend filter: `TREND_SERIES_ID`, `TREND_MA_WINDOW`
- Allocation: `ALLOCATION_MODEL`, `VOL_WINDOW_DAYS`, `TARGET_VOL_ANN`, `LEVERAGE_CAP`, `MACRO_MULTIPLIER_*`, `EQUITY_WEIGHT_*`
- Stale safety: `EXPECTED_LAG_DAYS_*`, `VALID_FOR_DAYS_*`, `HOLD_LAST_SCORE_ON_DEFCON_STALE`, `CRITICAL_SERIES_IDS`
