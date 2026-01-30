# Fear/Euphoria v2 Patch Notes (overlay-safe)

## What this patch adds
- Cycle-based FEAR/EUPHORIA **forecast windows** (24/36 months ahead) + confidence
- Confirm triggers that only fire **inside** the forecast window
  - FEAR: volatility spike + trend break + DEFCON2/1
  - EUPHORIA: very low vol + overextension + momentum deceleration
- Tiered FEAR defense levels (L1/L2/L3) used to scale leverage caps and risk multipliers
- Month-level forecast calendar outputs:
  - `data/fear_euphoria_calendar.csv`
  - `data/fear_euphoria_forecast.ics`
- Discord/Site messaging includes FEAR/EUPH snapshot fields when available

## Files overwritten/added by the update bundle
- `.env.example`, `README.md`
- `app/cycles.py` (new)
- `app/fear_euphoria.py` (new)
- `app/exporter.py`, `app/notifier.py`, `app/portfolio.py`, `app/settings.py`
- `scripts/run_daily.py`
- `site/app.js`, `site/index.html`, `site/styles.css`

## Environment variables (recommended rollout)
Add to your real env file (e.g. `/etc/warroom.env` or `.env`) as needed.

### Safe rollout (no portfolio behavior change)
- `PORTFOLIO_USE_FEAR_EUPHORIA=false`
- `PORTFOLIO_USE_CYCLES=false`

### Enable FEAR defense after validation
- `PORTFOLIO_USE_FEAR_EUPHORIA=true`

Tiered FEAR parameters (defaults):
- `PORTFOLIO_FEAR_L1_LEVERAGE_CAP=1.2`
- `PORTFOLIO_FEAR_L2_LEVERAGE_CAP=1.0`
- `PORTFOLIO_FEAR_L3_LEVERAGE_CAP=0.6`
- `PORTFOLIO_FEAR_L1_RISK_MULTIPLIER=0.85`
- `PORTFOLIO_FEAR_L2_RISK_MULTIPLIER=0.70`
- `PORTFOLIO_FEAR_L3_RISK_MULTIPLIER=0.50`

Trigger thresholds (defaults):
- `FEAR_EUPHORIA_FORECAST_WINDOW_M=36`
- `FEAR_VOL_SPIKE_Z=1.0`
- `FEAR_VOL_LOW_Z=-0.5`
- `FEAR_OVEREXT_PCT=0.15`
- `FEAR_MOM_FAST_DAYS=20`
- `FEAR_MOM_SLOW_DAYS=60`

## After applying
Run once to generate new outputs:
- `python scripts/run_daily.py --window-days 30`

Verify files exist:
- `data/fear_euphoria_daily.csv`
- `data/fear_euphoria_monthly.csv`
- `data/fear_euphoria_calendar.csv`
- `data/fear_euphoria_forecast.ics`

## Rollback
Fast rollback (recommended):
1) Stop the service
2) Restore the backup you created before patching
3) Start the service

Behavior-only rollback (if you only want to disable the new overlay):
- Set `PORTFOLIO_USE_FEAR_EUPHORIA=false` and restart the service
