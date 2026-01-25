from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app import db
from app.settings import get_settings


MIN_OBS = {
    "T10Y2Y": 60,
    "BAMLH0A0HYM2": 60,
    "COPPER_GOLD_RATIO": 205,
    "WEI": 4,
    "SAHMREALTIME": 1,
    "UMCSENT": 1,
}

# For early-history backfills, some series (e.g. WEI) simply do not exist yet.
# We still want to compute a state using the core sensors that are available.
REQUIRED_SERIES = {
    "T10Y2Y",
    "COPPER_GOLD_RATIO",
    "SAHMREALTIME",
    "UMCSENT",
}

@dataclass
class SnapshotResult:
    as_of_date: date
    score: Optional[float]
    reasons: List[str]
    health: Dict
    hard_defcon1: bool
    components: Dict[str, float]
    ready: bool
    trend: Dict


def _series_frame(df: pd.DataFrame, series_id: str, as_of_date: date, tz: ZoneInfo) -> pd.DataFrame:
    subset = df[df["series_id"] == series_id].copy()
    if subset.empty:
        return subset
    subset = subset[subset["as_of_date"] <= as_of_date]
    subset = subset.sort_values("timestamp")
    return subset


def build_snapshot(conn) -> SnapshotResult:
    settings = get_settings()
    expected_lag_days = {
        "1D": int(settings.expected_lag_days_1d),
        "1W": int(settings.expected_lag_days_1w),
        "1M": int(settings.expected_lag_days_1m),
    }
    valid_for_days = {
        "1D": int(settings.valid_for_days_1d),
        "1W": int(settings.valid_for_days_1w),
        "1M": int(settings.valid_for_days_1m),
    }
    tz = ZoneInfo(settings.as_of_tz)
    rows = db.fetch_observations(conn)
    if not rows:
        return SnapshotResult(
            as_of_date=date.today(),
            score=None,
            reasons=["no data available"],
            health={},
            hard_defcon1=False,
            components={},
            ready=False,
            trend={
                "series_id": settings.trend_series_id,
                "window": int(settings.trend_ma_window),
                "signal": "UNKNOWN",
                "price": None,
                "ma": None,
                "price_above_ma": None,
                "vol_window": int(settings.vol_window_days),
                "vol_ann": None,
            },
        )

    df = pd.DataFrame(rows, columns=["series_id", "time_utc_ms", "interval", "value"])
    df["timestamp"] = pd.to_datetime(df["time_utc_ms"], unit="ms", utc=True)
    df["as_of_date"] = df["timestamp"].dt.tz_convert(tz).dt.date

    as_of_date = df["as_of_date"].max()
    components: Dict[str, float] = {}
    reasons: List[str] = []
    health: Dict[str, Dict] = {}
    stale_flags: Dict[str, bool] = {}
    hard_defcon1 = False
    ready = True

    for series_id, need in MIN_OBS.items():
        series_df = _series_frame(df, series_id, as_of_date, tz)
        have = len(series_df)
        if series_df.empty:
            health[series_id] = {"have": have, "need": need, "stale": True, "reason": "missing"}
            if series_id in REQUIRED_SERIES:
                ready = False
            continue

        last_row = series_df.iloc[-1]
        last_ts: datetime = last_row["timestamp"].to_pydatetime().astimezone(tz)

        # available_at / valid_for_days model:
        # - "late": we're past the expected publication lag (warn-only)
        # - "stale": the observation is too old to be trusted for scoring (ignore)
        interval = str(last_row["interval"])
        as_of_dt = datetime.combine(as_of_date, datetime.min.time(), tzinfo=tz)
        delta_days = (as_of_dt - last_ts).days
        lag_days = expected_lag_days.get(interval, 2)
        valid_days = valid_for_days.get(interval, max(lag_days, 2))
        late = delta_days > lag_days
        stale = delta_days > valid_days

        # Track sufficiency in health, but do not block readiness on optional series.
        # Components already guard on lookbacks (e.g. MA200) and stale flags.
        if have < need and series_id in REQUIRED_SERIES:
            # Required series must exist, but we allow state computation even while it is still warming up.
            # This enables earlier daily state history (e.g. from 1988) while keeping scoring rules intact.
            pass

        stale_flags[series_id] = stale
        health[series_id] = {
            "have": have,
            "need": need,
            "interval": interval,
            "age_days": int(delta_days),
            "lag_days": int(lag_days),
            "valid_for_days": int(valid_days),
            "late": bool(late),
            "stale": bool(stale),
            "last": last_ts.isoformat(),
        }

    # Scoring stops if core data is not ready
    if not ready:
        return SnapshotResult(
            as_of_date=as_of_date,
            score=None,
            reasons=["data not ready"],
            health=health,
            hard_defcon1=False,
            components={},
            ready=False,
            trend={
                "series_id": settings.trend_series_id,
                "window": int(settings.trend_ma_window),
                "signal": "UNKNOWN",
                "price": None,
                "ma": None,
                "price_above_ma": None,
                "vol_window": int(settings.vol_window_days),
                "vol_ann": None,
            },
        )

    # Trend: NASDAQ close vs MA{window} (optional)
    trend = {
        "series_id": settings.trend_series_id,
        "window": int(settings.trend_ma_window),
        "signal": "UNKNOWN",
        "price": None,
        "ma": None,
        "price_above_ma": None,
        "vol_window": int(settings.vol_window_days),
        "vol_ann": None,
    }
    try:
        window = int(settings.trend_ma_window)
        vol_window = int(settings.vol_window_days)
        px_df = _series_frame(df, settings.trend_series_id, as_of_date, tz)
        if not px_df.empty:
            price = float(px_df.iloc[-1]["value"])
            trend["price"] = price
            if window > 1 and len(px_df) >= window:
                ma = float(px_df.tail(window)["value"].mean())
                trend["ma"] = ma
                above = price >= ma
                trend["price_above_ma"] = above
                trend["signal"] = "UP" if above else "DOWN"

            # Vol targeting input: realized volatility (annualized) over the last N trading days.
            if vol_window > 1 and len(px_df) >= (vol_window + 1):
                rets = px_df["value"].pct_change().tail(vol_window).dropna()
                if len(rets) >= vol_window:
                    vol_daily = float(rets.std())
                    if vol_daily > 0:
                        trend["vol_ann"] = float(vol_daily * math.sqrt(252))
    except Exception:
        # Keep UNKNOWN trend on any parsing/compute errors.
        pass

    # T10Y2Y: cross up from inversion
    t_df = _series_frame(df, "T10Y2Y", as_of_date, tz)
    if len(t_df) >= 2 and not stale_flags.get("T10Y2Y", False):
        prev_val = t_df.iloc[-2]["value"]
        last_val = t_df.iloc[-1]["value"]
        if prev_val < 0 <= last_val:
            w = float(settings.weight_t10y2y_cross_up)
            components["T10Y2Y_cross_up"] = w
            reasons.append(f"T10Y2Y un-inversion cross up (+{w:g})")
        else:
            components["T10Y2Y_cross_up"] = 0.0
    else:
        components["T10Y2Y_cross_up"] = 0.0

    # BAML spread: risk if elevated and rising
    b_df = _series_frame(df, "BAMLH0A0HYM2", as_of_date, tz)
    if not b_df.empty and not stale_flags.get("BAMLH0A0HYM2", False):
        last_val = b_df.iloc[-1]["value"]
        lookback = 20 if len(b_df) >= 20 else len(b_df) - 1
        slope = 0.0
        if lookback > 0:
            slope = last_val - b_df.iloc[-1 - lookback]["value"]
        if last_val >= 4.0 and slope >= 0:
            w = float(settings.weight_baml_spread_risk)
            components["BAML_spread_risk"] = w
            reasons.append(f"BAML spread high ({last_val:.2f}, slope {slope:.2f}) (+{w:g})")
        else:
            components["BAML_spread_risk"] = 0.0
    else:
        components["BAML_spread_risk"] = 0.0

    # WEI: recessionary recent trend
    w_df = _series_frame(df, "WEI", as_of_date, tz)
    if len(w_df) >= 4 and not stale_flags.get("WEI", False):
        recent_mean = float(w_df.tail(4)["value"].mean())
        last_val = w_df.iloc[-1]["value"]
        if last_val < 0 and recent_mean < 0:
            w = float(settings.weight_wei_recession_trend)
            components["WEI_recession_trend"] = w
            reasons.append(f"WEI negative trend ({recent_mean:.2f}) (+{w:g})")
        else:
            components["WEI_recession_trend"] = 0.0
    else:
        components["WEI_recession_trend"] = 0.0

    # COPPER/GOLD ratio: 5 days under MA200
    cg_df = _series_frame(df, "COPPER_GOLD_RATIO", as_of_date, tz)
    if len(cg_df) >= 200 and not stale_flags.get("COPPER_GOLD_RATIO", False):
        cg_df = cg_df.copy()
        cg_df["ma200"] = cg_df["value"].rolling(window=200).mean()
        tail = cg_df.tail(5)
        if (tail["value"] < tail["ma200"]).all():
            w = float(settings.weight_copper_gold_under_ma200)
            components["COPPER_GOLD_under_ma200"] = w
            reasons.append(f"Copper/Gold below MA200 for 5 days (+{w:g})")
        else:
            components["COPPER_GOLD_under_ma200"] = 0.0
    else:
        components["COPPER_GOLD_under_ma200"] = 0.0

    # UM consumer sentiment
    um_df = _series_frame(df, "UMCSENT", as_of_date, tz)
    if not um_df.empty and not stale_flags.get("UMCSENT", False):
        last_val = um_df.iloc[-1]["value"]
        if last_val < 65:
            w = float(settings.weight_umcsent_low)
            components["UMCSENT_low"] = w
            reasons.append(f"UM Sentiment low ({last_val:.1f}) (+{w:g})")
        else:
            components["UMCSENT_low"] = 0.0
    else:
        components["UMCSENT_low"] = 0.0

    # Sahm rule hard trigger
    s_df = _series_frame(df, "SAHMREALTIME", as_of_date, tz)
    if not s_df.empty and not stale_flags.get("SAHMREALTIME", False):
        last_val = s_df.iloc[-1]["value"]
        if last_val >= settings.sahm_hard_trigger_threshold:
            hard_defcon1 = True
            reasons.append(
                f"Sahm rule {last_val:.2f} >= {settings.sahm_hard_trigger_threshold:.2f} (hard DEFCON1)"
            )

    score = float(sum(components.values()))

    return SnapshotResult(
        as_of_date=as_of_date,
        score=score,
        reasons=reasons,
        health=health,
        hard_defcon1=hard_defcon1,
        components=components,
        ready=True,
        trend=trend,
    )
