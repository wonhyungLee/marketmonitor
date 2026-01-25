import argparse
import csv
import json
import math
import os
import sqlite3
import time
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# Mirror app/snapshot.py + app/engine.py logic without external deps (pandas/numpy).
MIN_OBS: Dict[str, int] = {
    "NASDAQ_DLY_IXIC": 200,
    "T10Y2Y": 60,
    "BAMLH0A0HYM2": 60,
    "COPPER_GOLD_RATIO": 205,
    "WEI": 4,
    "SAHMREALTIME": 1,
    "UMCSENT": 1,
}

EXPECTED_LAG_DAYS: Dict[str, int] = {
    "1D": 2,
    "1W": 10,
    "1M": 40,
}

# Validity window for "stale" (score ignores only when too old). Mirrors app/snapshot.py defaults.
VALID_FOR_DAYS: Dict[str, int] = {
    "1D": 4,
    "1W": 21,
    "1M": 62,
}

# v2.0 thresholds (kept in sync with app/engine.py)
DEFCON2_SCORE_THRESHOLD = 2.0
DEFCON1_SCORE_THRESHOLD = 3.5
NORMAL_SCORE_THRESHOLD = 2.0
DEFCON1_EXIT_SCORE_THRESHOLD = 3.0

# Hybrid model: trend + allocation (kept in sync with app/settings.py + app/engine.py defaults)
TREND_SERIES_ID = "NASDAQ_DLY_IXIC"
TREND_MA_WINDOW = 200

# Allocation model: fixed (table) or trend_vol_target (trend-following + vol targeting).
ALLOCATION_MODEL = str(os.getenv("ALLOCATION_MODEL", "trend_vol_target") or "").strip().lower()

# Vol targeting defaults (kept in sync with app/settings.py + app/engine.py defaults)
VOL_WINDOW_DAYS = int(os.getenv("VOL_WINDOW_DAYS", "20") or "20")
TARGET_VOL_ANN = float(os.getenv("TARGET_VOL_ANN", "0.41") or "0.41")
LEVERAGE_CAP = float(os.getenv("LEVERAGE_CAP", "2.0") or "2.0")
MACRO_MULTIPLIER_NORMAL = float(os.getenv("MACRO_MULTIPLIER_NORMAL", "1.0") or "1.0")
MACRO_MULTIPLIER_DEFCON2 = float(os.getenv("MACRO_MULTIPLIER_DEFCON2", "1.0") or "1.0")
MACRO_MULTIPLIER_DEFCON1 = float(os.getenv("MACRO_MULTIPLIER_DEFCON1", "1.0") or "1.0")

EQUITY_WEIGHT_NORMAL = 0.0
EQUITY_WEIGHT_DEFCON2_TREND_UP = 0.5
EQUITY_WEIGHT_DEFCON1_TREND_UP = 0.7
EQUITY_WEIGHT_DEFCON2_TREND_DOWN = 0.3
EQUITY_WEIGHT_DEFCON1_TREND_DOWN = 1.0
EQUITY_WEIGHT_DEFCON2_TREND_UNKNOWN = 0.4
EQUITY_WEIGHT_DEFCON1_TREND_UNKNOWN = 0.8

HOLD_LAST_SCORE_ON_DEFCON_STALE = True
CRITICAL_SERIES_IDS = (
    "T10Y2Y",
    "BAMLH0A0HYM2",
    "COPPER_GOLD_RATIO",
    "WEI",
    "SAHMREALTIME",
    "UMCSENT",
)

# Compute daily states even when some series do not exist historically (e.g. WEI).
# We require only these core series to be present to avoid producing results with too few inputs.
REQUIRED_SERIES = (
    "T10Y2Y",
    "COPPER_GOLD_RATIO",
    "SAHMREALTIME",
    "UMCSENT",
)

@dataclass(frozen=True)
class SeriesData:
    interval: str
    dates: List[date]
    values: List[float]
    prefix_sum: Optional[List[float]] = None  # used for MA200 (COPPER_GOLD_RATIO)


def _find_interval_csv(data_dir: Path, interval: str) -> Path:
    candidates = sorted(data_dir.glob(f"*{interval}.csv"))
    if not candidates:
        raise FileNotFoundError(f"no *{interval}.csv found under {data_dir}")
    return candidates[0]


def _read_rows(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def _parse_date(raw: str) -> date:
    return date.fromisoformat(str(raw).strip())


def _parse_float_maybe(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    try:
        return float(s)
    except Exception:
        return None


def _stale(last_obs_date: date, interval: str, as_of_date: date) -> bool:
    # Backward-compat wrapper; prefer _stale_status().
    return _stale_status(last_obs_date, interval, as_of_date)[1]


def _stale_status(last_obs_date: date, interval: str, as_of_date: date) -> Tuple[bool, bool, int, int, int]:
    """
    Returns: (late, stale, age_days, lag_days, valid_for_days)

    CSV exports are date-granular (no timestamp). The in-app logic uses datetime
    differences floored to whole days, which effectively gives ~1 day of buffer
    (important for weekends/holidays). Mirror that behavior with a +1 day buffer.
    """
    lag = int(EXPECTED_LAG_DAYS.get(interval, 2))
    valid = int(VALID_FOR_DAYS.get(interval, max(lag, 2)))
    delta_days = int((as_of_date - last_obs_date).days)
    late = delta_days > (lag + 1)
    stale = delta_days > (valid + 1)
    return late, stale, delta_days, lag, valid


def _ma200(prefix_sum: List[float], values: List[float], idx: int) -> Optional[float]:
    if idx < 199:
        return None
    total = prefix_sum[idx + 1] - prefix_sum[idx + 1 - 200]
    return total / 200.0


def _trend_signal(trend: Dict) -> str:
    sig = str((trend or {}).get("signal") or "UNKNOWN").upper()
    return sig if sig in ("UP", "DOWN", "UNKNOWN") else "UNKNOWN"


def _decide_allocation_fixed(state: str, trend: Dict) -> Tuple[str, Optional[float]]:
    sig = _trend_signal(trend)
    if state == "NORMAL":
        return "SELL / DISTRIBUTE", float(EQUITY_WEIGHT_NORMAL)
    if state == "DEFCON2":
        if sig == "UP":
            return "REBALANCE / ADJUST", float(EQUITY_WEIGHT_DEFCON2_TREND_UP)
        if sig == "DOWN":
            return "REBALANCE / ADJUST", float(EQUITY_WEIGHT_DEFCON2_TREND_DOWN)
        return "REBALANCE / ADJUST", float(EQUITY_WEIGHT_DEFCON2_TREND_UNKNOWN)
    if state == "DEFCON1":
        if sig == "UP":
            return "BUY / ACCUMULATE", float(EQUITY_WEIGHT_DEFCON1_TREND_UP)
        if sig == "DOWN":
            return "BUY / ACCUMULATE", float(EQUITY_WEIGHT_DEFCON1_TREND_DOWN)
        return "BUY / ACCUMULATE", float(EQUITY_WEIGHT_DEFCON1_TREND_UNKNOWN)
    return "WAIT / WARMUP", None


def _macro_multiplier(state: str) -> float:
    if state == "DEFCON1":
        return float(MACRO_MULTIPLIER_DEFCON1)
    if state == "DEFCON2":
        return float(MACRO_MULTIPLIER_DEFCON2)
    if state == "NORMAL":
        return float(MACRO_MULTIPLIER_NORMAL)
    return 0.0


def _decide_allocation_trend_vol_target(state: str, trend: Dict) -> Tuple[str, Optional[float]]:
    sig = _trend_signal(trend)
    if state == "WARMUP":
        return "WAIT / WARMUP", None

    if sig == "DOWN":
        return "DEFENSIVE / CASH", 0.0
    if sig != "UP":
        return _decide_allocation_fixed(state, trend)

    cap = float(LEVERAGE_CAP)
    if cap <= 0:
        cap = 1.0

    vol_ann = trend.get("vol_ann")
    if not isinstance(vol_ann, (int, float)) or float(vol_ann) <= 0:
        base = 1.0
    else:
        base = float(TARGET_VOL_ANN) / float(vol_ann)

    if base < 0:
        base = 0.0
    if base > cap:
        base = cap

    w = base * _macro_multiplier(state)
    if w < 0:
        w = 0.0
    if w > cap:
        w = cap

    if state == "NORMAL":
        return "BUY / ACCUMULATE", w
    if state in ("DEFCON2", "DEFCON1"):
        return "CAUTIOUS HOLD", w
    return "WAIT / WARMUP", None


def _decide_allocation(state: str, trend: Dict) -> Tuple[str, Optional[float]]:
    if ALLOCATION_MODEL in ("trend_vol_target", "vol_target", "voltarget", "vt"):
        return _decide_allocation_trend_vol_target(state, trend)
    return _decide_allocation_fixed(state, trend)


def _load_series(data_dir: Path) -> Dict[str, SeriesData]:
    """
    Load the 6 WarRoom series from 1D/1W/1M CSV exports.

    The folder currently contains combined exports (NASDAQ_DLY_IXIC, 1D/1W/1M.csv)
    where indicator values are present as columns. We intentionally ingest each
    series from the file that matches its intended interval:
      - 1D: T10Y2Y, HY_Spread, Copper_Gold_Log
      - 1W: WEI
      - 1M: Sahm_Rule, UM_Sentiment
    """

    daily_csv = _find_interval_csv(data_dir, "1D")
    weekly_csv = _find_interval_csv(data_dir, "1W")
    monthly_csv = _find_interval_csv(data_dir, "1M")

    # series_id -> date -> value
    by_series: Dict[str, Dict[date, float]] = {sid: {} for sid in MIN_OBS.keys()}

    # 1D
    for row in _read_rows(daily_csv):
        d = _parse_date(row.get("time", ""))
        for sid, col in (
            ("NASDAQ_DLY_IXIC", "close"),
            ("T10Y2Y", "T10Y2Y"),
            ("BAMLH0A0HYM2", "HY_Spread"),
            ("COPPER_GOLD_RATIO", "Copper_Gold_Log"),
        ):
            val = _parse_float_maybe(row.get(col))
            if val is None:
                continue
            by_series[sid][d] = val

    # 1W
    for row in _read_rows(weekly_csv):
        d = _parse_date(row.get("time", ""))
        val = _parse_float_maybe(row.get("WEI"))
        if val is None:
            continue
        by_series["WEI"][d] = val

    # 1M
    for row in _read_rows(monthly_csv):
        d = _parse_date(row.get("time", ""))
        sahm = _parse_float_maybe(row.get("Sahm_Rule"))
        if sahm is not None:
            by_series["SAHMREALTIME"][d] = sahm
        um = _parse_float_maybe(row.get("UM_Sentiment"))
        if um is not None:
            by_series["UMCSENT"][d] = um

    out: Dict[str, SeriesData] = {}
    for sid in MIN_OBS.keys():
        items = sorted(by_series[sid].items(), key=lambda kv: kv[0])
        dates = [d for d, _ in items]
        values = [float(v) for _, v in items]
        interval = {
            "NASDAQ_DLY_IXIC": "1D",
            "T10Y2Y": "1D",
            "BAMLH0A0HYM2": "1D",
            "COPPER_GOLD_RATIO": "1D",
            "WEI": "1W",
            "SAHMREALTIME": "1M",
            "UMCSENT": "1M",
        }[sid]

        prefix_sum = None
        if sid in ("COPPER_GOLD_RATIO", "NASDAQ_DLY_IXIC"):
            prefix_sum = [0.0]
            acc = 0.0
            for v in values:
                acc += float(v)
                prefix_sum.append(acc)

        out[sid] = SeriesData(interval=interval, dates=dates, values=values, prefix_sum=prefix_sum)

    return out


def _realized_vol_ann(values: List[float], idx: int, window: int, trading_days_per_year: int = 252) -> Optional[float]:
    if window <= 1 or idx < window:
        return None
    rets: List[float] = []
    start = idx - window + 1
    for j in range(start, idx + 1):
        prev = float(values[j - 1])
        cur = float(values[j])
        if prev == 0:
            return None
        rets.append(cur / prev - 1.0)
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return None
    return math.sqrt(var) * math.sqrt(float(trading_days_per_year))


def _snapshot_for_date(
    series: Dict[str, SeriesData], as_of_date: date
) -> Tuple[bool, Optional[float], bool, List[str], Dict[str, float], Dict, Dict]:
    """
    Returns: (ready, score, hard_defcon1, triggers, components, health, trend)
    """
    health: Dict[str, Dict] = {}
    stale_flags: Dict[str, bool] = {}
    ready = True

    # Health + readiness gate
    for sid, need in MIN_OBS.items():
        sd = series[sid]
        idx = bisect_right(sd.dates, as_of_date) - 1
        have = idx + 1
        if idx < 0:
            health[sid] = {"have": 0, "need": need, "stale": True, "reason": "missing"}
            stale_flags[sid] = True
            if sid in REQUIRED_SERIES:
                ready = False
            continue

        last_d = sd.dates[idx]
        late, stale, age_days, lag_days, valid_days = _stale_status(last_d, sd.interval, as_of_date)
        stale_flags[sid] = stale
        health[sid] = {
            "have": have,
            "need": need,
            "interval": sd.interval,
            "age_days": age_days,
            "lag_days": lag_days,
            "valid_for_days": valid_days,
            "late": late,
            "stale": stale,
            "last": last_d.isoformat(),
        }
        # Keep have/need for reporting, but do not block readiness on optional series
        # (components already guard their own lookbacks).

    if not ready:
        return (
            False,
            None,
            False,
            ["data not ready"],
            {},
            health,
            {
                "series_id": TREND_SERIES_ID,
                "window": TREND_MA_WINDOW,
                "signal": "UNKNOWN",
                "price": None,
                "ma": None,
                "price_above_ma": None,
                "vol_window": VOL_WINDOW_DAYS,
                "vol_ann": None,
            },
        )

    triggers: List[str] = []
    components: Dict[str, float] = {}
    hard_defcon1 = False

    # Trend: NASDAQ close vs MA{window} (optional)
    trend = {
        "series_id": TREND_SERIES_ID,
        "window": TREND_MA_WINDOW,
        "signal": "UNKNOWN",
        "price": None,
        "ma": None,
        "price_above_ma": None,
        "vol_window": VOL_WINDOW_DAYS,
        "vol_ann": None,
    }
    try:
        px = series[TREND_SERIES_ID]
        px_idx = bisect_right(px.dates, as_of_date) - 1
        if px_idx >= 0 and not stale_flags.get(TREND_SERIES_ID, False):
            price = float(px.values[px_idx])
            trend["price"] = price
            if px.prefix_sum is not None and px_idx >= (TREND_MA_WINDOW - 1):
                ma = _ma200(px.prefix_sum, px.values, px_idx)
                if ma is not None:
                    trend["ma"] = float(ma)
                    above = price >= float(ma)
                    trend["price_above_ma"] = above
                    trend["signal"] = "UP" if above else "DOWN"

            if VOL_WINDOW_DAYS > 1 and px_idx >= VOL_WINDOW_DAYS:
                v = _realized_vol_ann(px.values, px_idx, VOL_WINDOW_DAYS)
                if v is not None and v > 0:
                    trend["vol_ann"] = float(v)
    except Exception:
        pass

    # T10Y2Y: cross up from inversion
    t = series["T10Y2Y"]
    t_idx = bisect_right(t.dates, as_of_date) - 1
    if t_idx >= 1 and not stale_flags.get("T10Y2Y", False):
        prev_val = t.values[t_idx - 1]
        last_val = t.values[t_idx]
        if prev_val < 0 <= last_val:
            components["T10Y2Y_cross_up"] = 2.0
            triggers.append("T10Y2Y un-inversion cross up (+2)")
        else:
            components["T10Y2Y_cross_up"] = 0.0
    else:
        components["T10Y2Y_cross_up"] = 0.0

    # BAML spread: risk if elevated and rising
    b = series["BAMLH0A0HYM2"]
    b_idx = bisect_right(b.dates, as_of_date) - 1
    if b_idx >= 0 and not stale_flags.get("BAMLH0A0HYM2", False):
        last_val = b.values[b_idx]
        have = b_idx + 1
        lookback = 20 if have >= 20 else have - 1
        slope = 0.0
        if lookback > 0:
            slope = last_val - b.values[b_idx - lookback]
        if last_val >= 4.0 and slope >= 0:
            components["BAML_spread_risk"] = 1.5
            triggers.append(f"BAML spread high ({last_val:.2f}, slope {slope:.2f}) (+1.5)")
        else:
            components["BAML_spread_risk"] = 0.0
    else:
        components["BAML_spread_risk"] = 0.0

    # WEI: recessionary recent trend
    w = series["WEI"]
    w_idx = bisect_right(w.dates, as_of_date) - 1
    if w_idx >= 3 and not stale_flags.get("WEI", False):
        last_val = w.values[w_idx]
        recent_mean = sum(w.values[w_idx - 3 : w_idx + 1]) / 4.0
        if last_val < 0 and recent_mean < 0:
            components["WEI_recession_trend"] = 1.0
            triggers.append(f"WEI negative trend ({recent_mean:.2f}) (+1)")
        else:
            components["WEI_recession_trend"] = 0.0
    else:
        components["WEI_recession_trend"] = 0.0

    # COPPER/GOLD ratio: 5 days under MA200
    cg = series["COPPER_GOLD_RATIO"]
    cg_idx = bisect_right(cg.dates, as_of_date) - 1
    if cg_idx >= 0 and not stale_flags.get("COPPER_GOLD_RATIO", False):
        have = cg_idx + 1
        ok = False
        if have >= 200 and cg.prefix_sum is not None and cg_idx >= 4:
            ok = True
            for j in range(cg_idx - 4, cg_idx + 1):
                ma = _ma200(cg.prefix_sum, cg.values, j)
                if ma is None or not (cg.values[j] < ma):
                    ok = False
                    break
        if ok:
            components["COPPER_GOLD_under_ma200"] = 0.5
            triggers.append("Copper/Gold below MA200 for 5 days (+0.5)")
        else:
            components["COPPER_GOLD_under_ma200"] = 0.0
    else:
        components["COPPER_GOLD_under_ma200"] = 0.0

    # UM consumer sentiment
    um = series["UMCSENT"]
    um_idx = bisect_right(um.dates, as_of_date) - 1
    if um_idx >= 0 and not stale_flags.get("UMCSENT", False):
        last_val = um.values[um_idx]
        if last_val < 65:
            components["UMCSENT_low"] = 0.5
            triggers.append(f"UM Sentiment low ({last_val:.1f}) (+0.5)")
        else:
            components["UMCSENT_low"] = 0.0
    else:
        components["UMCSENT_low"] = 0.0

    # Sahm rule hard trigger
    s = series["SAHMREALTIME"]
    s_idx = bisect_right(s.dates, as_of_date) - 1
    if s_idx >= 0 and not stale_flags.get("SAHMREALTIME", False):
        last_val = s.values[s_idx]
        if last_val >= 0.5:
            hard_defcon1 = True
            triggers.append(f"Sahm rule {last_val:.2f} >= 0.50 (hard DEFCON1)")

    score = float(sum(components.values()))
    return True, score, hard_defcon1, triggers, components, health, trend


def _ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_states (
            as_of_date TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            score REAL NULL,
            reasons_json TEXT,
            health_json TEXT,
            created_at TEXT NOT NULL
        );
        """
    )


def _write_sqlite(db_path: Path, rows: List[Tuple[str, str, Optional[float], str, str, str]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_sqlite_schema(conn)
        conn.execute("BEGIN;")
        conn.executemany(
            """
            INSERT OR REPLACE INTO daily_states
            (as_of_date, state, score, reasons_json, health_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest WarRoom v2.0 Hybrid daily states from CSV exports")
    parser.add_argument("--data-dir", type=Path, default=Path("지표데이터"), help="directory containing CSV exports")
    parser.add_argument("--start-date", type=str, default="1970-01-01", help="YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, default="", help="YYYY-MM-DD (default: max date in data)")
    parser.add_argument("--out-dir", type=Path, default=Path("data"), help="output directory")
    args = parser.parse_args()

    t0 = time.time()
    series = _load_series(args.data_dir)

    # Determine end date from data (unless provided)
    max_dates = [sd.dates[-1] for sd in series.values() if sd.dates]
    if not max_dates:
        raise RuntimeError(f"no usable observations found under {args.data_dir}")
    data_max = max(max_dates)

    start_d = _parse_date(args.start_date)
    end_d = _parse_date(args.end_date) if args.end_date else data_max
    if end_d < start_d:
        raise ValueError("end-date must be >= start-date")

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "market_states_daily.csv"
    out_jsonl = out_dir / "market_states_daily.jsonl"
    out_db = out_dir / "market_states.sqlite3"

    # Rolling streak counters over the computed daily score series.
    streak_ge_2 = 0
    streak_ge_3_5 = 0
    streak_lt_2 = 0
    streak_le_3 = 0

    prev_state = "NORMAL"
    prev_raw_score_for_hold: Optional[float] = None
    created_at = datetime.now(tz=timezone.utc).isoformat()

    sqlite_rows: List[Tuple[str, str, Optional[float], str, str, str]] = []

    # Stream JSONL + build CSV rows
    with out_csv.open("w", encoding="utf-8", newline="") as f_csv, out_jsonl.open("w", encoding="utf-8") as f_jsonl:
        fieldnames = [
            "as_of_date",
            "state",
            "score",
            "action",
            "equity_weight",
            "trend_signal",
            "trend_price",
            "trend_ma",
            "hard_defcon1",
            "prev_state",
            "streak_ge_2",
            "streak_ge_3_5",
            "streak_lt_2",
            "streak_le_3",
            "T10Y2Y_cross_up",
            "BAML_spread_risk",
            "WEI_recession_trend",
            "COPPER_GOLD_under_ma200",
            "UMCSENT_low",
            "triggers",
        ]
        writer = csv.DictWriter(f_csv, fieldnames=fieldnames)
        writer.writeheader()

        cur = start_d
        total_days = (end_d - start_d).days + 1
        processed = 0

        while cur <= end_d:
            ready, score, hard_defcon1, triggers, components, health, trend = _snapshot_for_date(series, cur)

            if not ready:
                state = "WARMUP"
                day_score: Optional[float] = None
                raw_score: Optional[float] = None
                effective_score: Optional[float] = None
                observed_raw_score: Optional[float] = None
                held_last_score = False
                stale_critical: List[str] = []
                action = "WAIT / WARMUP"
                equity_weight: Optional[float] = None
                # WARMUP breaks streaks (matches app/engine._streak behavior).
                streak_ge_2 = streak_ge_3_5 = streak_lt_2 = streak_le_3 = 0
                reasons = {"triggers": triggers, "components": components, "trend": trend}
                prev_state_for_day = prev_state
                prev_raw_score_for_hold = None
            else:
                raw_score = float(score) if score is not None else None
                observed_raw_score = raw_score

                prev_state_for_day = "NORMAL" if prev_state == "WARMUP" else prev_state

                stale_critical = []
                for sid in CRITICAL_SERIES_IDS:
                    info = health.get(sid) or {}
                    if info.get("stale") and int(info.get("have") or 0) > 0:
                        stale_critical.append(sid)

                held_last_score = False
                if (
                    HOLD_LAST_SCORE_ON_DEFCON_STALE
                    and prev_state_for_day in ("DEFCON1", "DEFCON2")
                    and stale_critical
                    and prev_raw_score_for_hold is not None
                    and raw_score is not None
                    and float(raw_score) < float(prev_raw_score_for_hold)
                ):
                    raw_score = float(prev_raw_score_for_hold)
                    held_last_score = True
                    triggers = list(triggers)
                    triggers.append(f"Held last score (DEFCON safety) due to stale: {', '.join(stale_critical)}")

                effective_score = raw_score
                if hard_defcon1 and raw_score is not None and raw_score < DEFCON1_SCORE_THRESHOLD:
                    # Display: avoid "DEFCON1 with score 0.5" confusion when hard-triggering.
                    effective_score = DEFCON1_SCORE_THRESHOLD
                day_score = effective_score

                # Update streaks with today's score (matches app/engine._streak over history_scores + current).
                if raw_score is None:
                    streak_ge_2 = streak_ge_3_5 = streak_lt_2 = streak_le_3 = 0
                else:
                    streak_ge_2 = streak_ge_2 + 1 if raw_score >= DEFCON2_SCORE_THRESHOLD else 0
                    streak_ge_3_5 = streak_ge_3_5 + 1 if raw_score >= DEFCON1_SCORE_THRESHOLD else 0
                    streak_lt_2 = streak_lt_2 + 1 if raw_score < NORMAL_SCORE_THRESHOLD else 0
                    streak_le_3 = streak_le_3 + 1 if raw_score <= DEFCON1_EXIT_SCORE_THRESHOLD else 0

                state = prev_state_for_day
                if hard_defcon1:
                    state = "DEFCON1"
                else:
                    if state in ("NORMAL", "WARMUP"):
                        if streak_ge_2 >= 3:
                            state = "DEFCON2"
                        if streak_ge_3_5 >= 3:
                            state = "DEFCON1"
                    elif state == "DEFCON2":
                        if streak_ge_3_5 >= 3:
                            state = "DEFCON1"
                        elif streak_lt_2 >= 5:
                            state = "NORMAL"
                    elif state == "DEFCON1":
                        if streak_le_3 >= 10:
                            state = "DEFCON2"

                reasons = {
                    "triggers": triggers,
                    "components": components,
                    "raw_score": raw_score,
                    "effective_score": effective_score,
                    "observed_raw_score": observed_raw_score,
                    "held_last_score": held_last_score,
                    "stale_critical_series": stale_critical,
                    "streaks": {
                        "streak_ge_2": streak_ge_2,
                        "streak_ge_3_5": streak_ge_3_5,
                        "streak_lt_2": streak_lt_2,
                        "streak_le_3": streak_le_3,
                    },
                    "prev_state": prev_state_for_day,
                    "trend": trend,
                }

                action, equity_weight = _decide_allocation(state, trend)
                reasons["allocation"] = {
                    "action": action,
                    "equity_weight": equity_weight,
                    "equity_weight_pct": None if equity_weight is None else round(float(equity_weight) * 100.0, 1),
                }

                prev_raw_score_for_hold = raw_score

            # Persist row
            as_of_str = cur.isoformat()
            reasons_json = json.dumps(reasons, ensure_ascii=False)
            health_json = json.dumps(health, ensure_ascii=False)
            sqlite_rows.append((as_of_str, state, day_score, reasons_json, health_json, created_at))

            writer.writerow(
                {
                    "as_of_date": as_of_str,
                    "state": state,
                    "score": "" if day_score is None else f"{day_score:.3f}",
                    "action": action,
                    "equity_weight": "" if equity_weight is None else f"{float(equity_weight):.3f}",
                    "trend_signal": _trend_signal(trend),
                    "trend_price": "" if trend.get("price") is None else f"{float(trend.get('price')):.2f}",
                    "trend_ma": "" if trend.get("ma") is None else f"{float(trend.get('ma')):.2f}",
                    "hard_defcon1": "1" if hard_defcon1 else "0",
                    "prev_state": prev_state_for_day,
                    "streak_ge_2": str(streak_ge_2),
                    "streak_ge_3_5": str(streak_ge_3_5),
                    "streak_lt_2": str(streak_lt_2),
                    "streak_le_3": str(streak_le_3),
                    "T10Y2Y_cross_up": f"{components.get('T10Y2Y_cross_up', 0.0):.2f}",
                    "BAML_spread_risk": f"{components.get('BAML_spread_risk', 0.0):.2f}",
                    "WEI_recession_trend": f"{components.get('WEI_recession_trend', 0.0):.2f}",
                    "COPPER_GOLD_under_ma200": f"{components.get('COPPER_GOLD_under_ma200', 0.0):.2f}",
                    "UMCSENT_low": f"{components.get('UMCSENT_low', 0.0):.2f}",
                    "triggers": " | ".join(triggers),
                }
            )

            f_jsonl.write(
                json.dumps(
                    {
                        "as_of_date": as_of_str,
                        "state": state,
                        "score": day_score,
                        "reasons": reasons,
                        "health": health,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

            prev_state = state

            processed += 1
            if processed % 2000 == 0 or processed == total_days:
                elapsed = time.time() - t0
                print(f"[{processed}/{total_days}] as_of={as_of_str} state={state} elapsed={elapsed:.1f}s")

            cur += timedelta(days=1)

    _write_sqlite(out_db, sqlite_rows)

    elapsed = time.time() - t0
    print(f"done: {start_d.isoformat()}..{end_d.isoformat()} ({len(sqlite_rows)} days) in {elapsed:.1f}s")
    print(f"wrote: {out_csv}")
    print(f"wrote: {out_jsonl}")
    print(f"wrote: {out_db}")


if __name__ == "__main__":
    main()
