from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
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
    health: Dict[str, Any]
    hard_defcon1: bool
    components: Dict[str, float]
    ready: bool
    trend: Dict[str, Any]


def _series_frame(df: pd.DataFrame, series_id: str, as_of_date: date, tz: ZoneInfo) -> pd.DataFrame:
    subset = df[df["series_id"] == series_id].copy()
    if subset.empty:
        return subset
    subset = subset[subset["as_of_date"] <= as_of_date]
    subset = subset.sort_values("timestamp")
    return subset


def build_snapshot(conn, as_of_date: Optional[date] = None, data_frame: Optional[pd.DataFrame] = None) -> SnapshotResult:
    settings = get_settings()
    tz = ZoneInfo(settings.as_of_tz)

    df = data_frame
    if df is None:
        rows = db.fetch_observations(conn)
        if not rows:
            return SnapshotResult(
                as_of_date=date.today(), score=None, reasons=["no data"], health={},
                hard_defcon1=False, components={}, ready=False, trend={}
            )
        df = pd.DataFrame(rows, columns=["series_id", "time_utc_ms", "interval", "value", "received_at"])

    if "timestamp" not in df.columns:
        if bool(getattr(settings, "use_received_at_for_latest", False)):
            ts = pd.to_datetime(df["received_at"], utc=True, errors="coerce")
            fallback = pd.to_datetime(df["time_utc_ms"], unit="ms", utc=True)
            df["timestamp"] = ts.fillna(fallback)
        else:
            df["timestamp"] = pd.to_datetime(df["time_utc_ms"], unit="ms", utc=True)
    if "as_of_date" not in df.columns:
        df["as_of_date"] = df["timestamp"].dt.tz_convert(tz).dt.date

    if isinstance(as_of_date, str):
        as_of_date = date.fromisoformat(as_of_date)

    if as_of_date is None:
        as_of_date = df["as_of_date"].max()
    reasons: List[str] = []
    components: Dict[str, float] = {}
    health: Dict[str, Any] = {}
    
    # 헬스 체크 및 세그먼트 데이터 준비
    series_data: Dict[str, pd.DataFrame] = {}
    stale_flags: Dict[str, bool] = {}
    ready = True

    for sid in MIN_OBS.keys():
        s_df = _series_frame(df, sid, as_of_date, tz)
        series_data[sid] = s_df
        
        if s_df.empty:
            if sid in REQUIRED_SERIES: ready = False
            health[sid] = {"stale": True, "reason": "missing"}
            continue

        # Stale 판단 로직 (LKV 적용 전 단계)
        last_ts = s_df.iloc[-1]["timestamp"].to_pydatetime().astimezone(tz)
        as_of_dt = datetime.combine(as_of_date, datetime.min.time(), tzinfo=tz)
        age_days = (as_of_dt - last_ts).days
        
        # 임계값 (기본 4일, 월간 데이터는 62일)
        limit = 62 if sid in ["UMCSENT", "SAHMREALTIME"] else 4
        stale = age_days > limit
        stale_flags[sid] = stale
        health[sid] = {"have": len(s_df), "age_days": age_days, "stale": stale}

    if not ready:
        return SnapshotResult(as_of_date=as_of_date, score=None, reasons=["required data missing"],
                              health=health, hard_defcon1=False, components={}, ready=False, trend={})

    # --- 퀀트 로직 고도화 ---

    # 1. EWMA Volatility 및 Trend (MA200)
    trend = {"signal": "UNKNOWN", "vol_ann": None}
    px_df = _series_frame(df, settings.trend_series_id, as_of_date, tz)
    if len(px_df) >= 200:
        price = float(px_df.iloc[-1]["value"])
        ma200 = float(px_df.tail(200)["value"].mean())
        
        # EWMA Volatility: 최근 변동성에 더 높은 가중치 (span=20)
        returns = px_df["value"].pct_change().tail(21).dropna()
        ewma_vol = float(returns.ewm(span=20).std().iloc[-1] * math.sqrt(252))
        
        trend.update({"price": price, "ma": ma200, "signal": "UP" if price >= ma200 else "DOWN", "vol_ann": ewma_vol})

    # 2. LKV (Last Known Value) Helper
    def get_lkv_value(sid: str) -> Optional[float]:
        s_df = series_data.get(sid)
        if s_df is None or s_df.empty: return None
        if stale_flags.get(sid):
            reasons.append(f"Warning: {sid} is stale. Using LKV (Last Known Value).")
        return float(s_df.iloc[-1]["value"])

    # 3. Scoring with LKV
    # T10Y2Y
    t_val = get_lkv_value("T10Y2Y")
    t_df = series_data["T10Y2Y"]
    if t_val is not None and len(t_df) >= 2:
        if t_df.iloc[-2]["value"] < 0 <= t_val:
            components["T10Y2Y_cross_up"] = settings.weight_t10y2y_cross_up
            reasons.append(f"T10Y2Y un-inversion (+{settings.weight_t10y2y_cross_up})")

    # BAML Spread
    b_val = get_lkv_value("BAMLH0A0HYM2")
    if b_val is not None and b_val >= 4.0:
        components["BAML_spread_risk"] = settings.weight_baml_spread_risk
        reasons.append(f"BAML Spread risk (+{settings.weight_baml_spread_risk})")

    # WEI
    w_df = series_data["WEI"]
    if not w_df.empty:
        w_val = get_lkv_value("WEI")
        if w_val is not None and w_val < 0:
            components["WEI_recession_trend"] = settings.weight_wei_recession_trend
            reasons.append(f"WEI negative (+{settings.weight_wei_recession_trend})")

    # COPPER/GOLD
    cg_df = series_data["COPPER_GOLD_RATIO"]
    if len(cg_df) >= 200:
        ma = cg_df["value"].rolling(200).mean().iloc[-1]
        if cg_df.iloc[-1]["value"] < ma:
            components["COPPER_GOLD_under_ma200"] = settings.weight_copper_gold_under_ma200
            reasons.append(f"Copper/Gold bearish (+{settings.weight_copper_gold_under_ma200})")

    # UMCSENT
    um_val = get_lkv_value("UMCSENT")
    if um_val is not None and um_val < 65:
        components["UMCSENT_low"] = settings.weight_umcsent_low
        reasons.append(f"UM Sentiment low (+{settings.weight_umcsent_low})")

    # Sahm Rule (Hard Trigger)
    hard_defcon1 = False
    sahm_val = get_lkv_value("SAHMREALTIME")
    if sahm_val is not None and sahm_val >= settings.sahm_hard_trigger_threshold:
        hard_defcon1 = True
        reasons.append("Sahm Rule Hard Trigger (DEFCON1)")

    return SnapshotResult(
        as_of_date=as_of_date,
        score=float(sum(components.values())),
        reasons=reasons,
        health=health,
        hard_defcon1=hard_defcon1,
        components=components,
        ready=True,
        trend=trend
    )
