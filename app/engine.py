from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from app import db
from app.settings import get_settings
from app.snapshot import build_snapshot
from app.portfolio import recommend_portfolio


logger = logging.getLogger("warroom.engine")


def _raw_score_from_state_row(row) -> Optional[float]:
    """Use raw score for streak logic even if we store a display score in DB."""
    try:
        reasons_json = row["reasons_json"]
        if reasons_json:
            reasons = json.loads(reasons_json)
            raw = reasons.get("raw_score")
            if raw is not None:
                return float(raw)
    except Exception:
        pass
    return row["score"]


def _streak(scores: List[Optional[float]], predicate) -> int:
    count = 0
    for val in reversed(scores):
        if val is None:
            break
        if predicate(val):
            count += 1
        else:
            break
    return count


@dataclass
class EngineResult:
    as_of_date: str
    state: str
    score: Optional[float]
    reasons: Dict
    health: Dict


def _parse_csv_list(raw: str) -> List[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def _trend_signal(trend: Dict) -> str:
    sig = str((trend or {}).get("signal") or "UNKNOWN").upper()
    return sig if sig in ("UP", "DOWN", "UNKNOWN") else "UNKNOWN"


def _decide_allocation_fixed(state: str, trend: Dict) -> Tuple[str, Optional[float]]:
    """Fixed table allocation (macro state + trend buckets)."""
    settings = get_settings()
    sig = _trend_signal(trend)

    if state == "NORMAL":
        return "SELL / DISTRIBUTE", float(settings.equity_weight_normal)
    if state == "DEFCON2":
        if sig == "UP":
            return "REBALANCE / ADJUST", float(settings.equity_weight_defcon2_trend_up)
        if sig == "DOWN":
            return "REBALANCE / ADJUST", float(settings.equity_weight_defcon2_trend_down)
        return "REBALANCE / ADJUST", float(settings.equity_weight_defcon2_trend_unknown)
    if state == "DEFCON1":
        if sig == "UP":
            return "BUY / ACCUMULATE", float(settings.equity_weight_defcon1_trend_up)
        if sig == "DOWN":
            return "BUY / ACCUMULATE", float(settings.equity_weight_defcon1_trend_down)
        return "BUY / ACCUMULATE", float(settings.equity_weight_defcon1_trend_unknown)

    # WARMUP / unknown state
    return "WAIT / WARMUP", None


def _macro_multiplier(state: str) -> float:
    settings = get_settings()
    if state == "DEFCON1":
        return float(settings.macro_multiplier_defcon1)
    if state == "DEFCON2":
        return float(settings.macro_multiplier_defcon2)
    if state == "NORMAL":
        return float(settings.macro_multiplier_normal)
    return 0.0


def _decide_allocation_trend_vol_target(state: str, trend: Dict) -> Tuple[str, Optional[float]]:
    """
    Trend-following + volatility targeting (long-only).

    - If Trend=DOWN: go defensive (0% equity).
    - If Trend=UP: size exposure by target_vol / realized_vol (capped), then apply optional macro multiplier.
    - If Trend info missing: fall back to fixed table allocation.
    """
    settings = get_settings()
    sig = _trend_signal(trend)

    if state == "WARMUP":
        return "WAIT / WARMUP", None

    if sig == "DOWN":
        return "DEFENSIVE / CASH", 0.0
    if sig != "UP":
        return _decide_allocation_fixed(state, trend)

    cap = float(settings.leverage_cap)
    if cap <= 0:
        cap = 1.0

    vol_ann = trend.get("vol_ann")
    if not isinstance(vol_ann, (int, float)) or float(vol_ann) <= 0:
        base = 1.0
    else:
        base = float(settings.target_vol_ann) / float(vol_ann)

    if base < 0:
        base = 0.0
    if base > cap:
        base = cap

    mult = _macro_multiplier(state)
    w = base * mult
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
    """Hybrid posture/allocation. Macro state is unchanged; this outputs action + equity weight."""
    settings = get_settings()
    model = str(getattr(settings, "allocation_model", "") or "").strip().lower()
    if model in ("trend_vol_target", "vol_target", "voltarget", "vt"):
        return _decide_allocation_trend_vol_target(state, trend)
    return _decide_allocation_fixed(state, trend)


def evaluate(conn, as_of_date: Optional[date] = None, data_frame=None) -> EngineResult:
    snapshot = build_snapshot(conn, as_of_date=as_of_date, data_frame=data_frame)
    settings = get_settings()

    defcon2_th = float(settings.defcon2_score_threshold)
    defcon1_th = float(settings.defcon1_score_threshold)
    normal_th = float(settings.normal_score_threshold)
    defcon1_exit_th = float(settings.defcon1_exit_score_threshold)

    raw_score = snapshot.score
    observed_raw_score = raw_score

    target_date = snapshot.as_of_date if as_of_date is None else as_of_date
    recent_states = db.fetch_recent_states_upto(conn, str(target_date), limit=30)
    prev_state = recent_states[0]["state"] if recent_states else "NORMAL"
    prev_state = "NORMAL" if (snapshot.ready and prev_state == "WARMUP") else prev_state
    prev_raw_score = _raw_score_from_state_row(recent_states[0]) if recent_states else None

    critical_series = _parse_csv_list(settings.critical_series_ids)
    stale_critical = []
    for sid in critical_series:
        info = snapshot.health.get(sid)
        if not info:
            continue
        if info.get("stale") and int(info.get("have") or 0) > 0:
            stale_critical.append(sid)

    held_last_score = False
    if (
        bool(settings.hold_last_score_on_defcon_stale)
        and prev_state in ("DEFCON1", "DEFCON2")
        and stale_critical
        and prev_raw_score is not None
        and raw_score is not None
        and float(raw_score) < float(prev_raw_score)
    ):
        # Safety: in a risk regime, don't let missing/expired sensors drive score down.
        raw_score = float(prev_raw_score)
        held_last_score = True

    effective_score = raw_score
    if snapshot.hard_defcon1 and raw_score is not None and raw_score < defcon1_th:
        # Display: avoid "DEFCON1 with score 0.5" confusion when hard-triggering.
        effective_score = defcon1_th

    # WARMUP: insufficient data
    if not snapshot.ready:
        now = datetime.now(tz=timezone.utc).isoformat()
        db.insert_daily_state(
            conn=conn,
            as_of_date=str(snapshot.as_of_date),
            state="WARMUP",
            score=None,
            reasons={"triggers": snapshot.reasons, "components": snapshot.components},
            health=snapshot.health,
            created_at=now,
        )
        return EngineResult(
            as_of_date=str(snapshot.as_of_date),
            state="WARMUP",
            score=None,
            reasons={"triggers": snapshot.reasons, "components": snapshot.components},
            health=snapshot.health,
        )

    history_scores: List[Optional[float]] = [_raw_score_from_state_row(row) for row in reversed(recent_states)]
    history_scores.append(raw_score)

    streak_ge_2 = _streak(history_scores, lambda s: s is not None and s >= defcon2_th)
    streak_ge_3_5 = _streak(history_scores, lambda s: s is not None and s >= defcon1_th)
    streak_lt_2 = _streak(history_scores, lambda s: s is not None and s < normal_th)
    streak_le_3 = _streak(history_scores, lambda s: s is not None and s <= defcon1_exit_th)

    state = prev_state
    if snapshot.hard_defcon1:
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
        "triggers": snapshot.reasons,
        "components": snapshot.components,
        "thresholds": {
            "defcon2_score_threshold": defcon2_th,
            "defcon1_score_threshold": defcon1_th,
            "normal_score_threshold": normal_th,
            "defcon1_exit_score_threshold": defcon1_exit_th,
        },
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
        "prev_state": prev_state,
        "trend": snapshot.trend,
    }

    action, equity_weight = _decide_allocation(state, snapshot.trend)
    reasons["allocation"] = {
        "action": action,
        "equity_weight": equity_weight,
        "equity_weight_pct": None if equity_weight is None else round(float(equity_weight) * 100.0, 1),
    }

    # Multi-asset portfolio recommendation (optional; driven by DB asset prices or CSV fallback).
    try:
        rec = recommend_portfolio(snapshot.as_of_date, state, conn)
        if rec is not None:
            reasons["portfolio"] = {
                "as_of_date": rec.as_of_date,
                "portfolio_date": rec.portfolio_date,
                "model": rec.model,
                "target_vol_ann": rec.target_vol_ann,
                "vol_window_days": rec.vol_window_days,
                "ma_window_days": rec.ma_window_days,
                "leverage_cap": rec.leverage_cap,
                "gross_exposure": rec.gross_exposure,
                "cash_weight": rec.cash_weight,
                "weights": rec.weights,
            }
            try:
                db.upsert_portfolio_daily(
                    conn=conn,
                    as_of_date=str(snapshot.as_of_date),
                    portfolio_date=rec.portfolio_date,
                    state=state,
                    model=rec.model,
                    target_vol_ann=rec.target_vol_ann,
                    vol_window_days=rec.vol_window_days,
                    ma_window_days=rec.ma_window_days,
                    leverage_cap=rec.leverage_cap,
                    gross_exposure=rec.gross_exposure,
                    cash_weight=rec.cash_weight,
                    created_at=datetime.now(tz=timezone.utc).isoformat(),
                )
                db.replace_portfolio_weights(conn, str(snapshot.as_of_date), rec.weights)
            except Exception:
                logger.warning("failed to persist portfolio recommendation", exc_info=True)
    except Exception:
        # Portfolio rec is best-effort; never block core state generation.
        pass

    if held_last_score and stale_critical:
        reasons["triggers"] = list(reasons.get("triggers") or [])
        reasons["triggers"].append(f"Held last score (DEFCON safety) due to stale: {', '.join(stale_critical)}")

    db.insert_daily_state(
        conn=conn,
        as_of_date=str(snapshot.as_of_date),
        state=state,
        score=effective_score,
        reasons=reasons,
        health=snapshot.health,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
    )

    return EngineResult(
        as_of_date=str(snapshot.as_of_date),
        state=state,
        score=effective_score,
        reasons=reasons,
        health=snapshot.health,
    )
