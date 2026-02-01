from __future__ import annotations

"""
Timing v1 (fixed): estimate WHEN Crisis/Fear and Euphoria/Overheat are likely to START.

Design goals
- Work directly off exported `data/fear_euphoria_daily.csv` (cycle windows + trigger flags).
- Produce `data/timing_v1_daily.csv` with:
    * within-horizon probabilities (cumulative)
    * ETA median date
    * "likely window" (central 50% interval)

This is intentionally lightweight (NumPy/Pandas only).
It does NOT fit ML models; it converts cycle "months_until_*" + confidence + trigger boosts
into a probability distribution over time using a lognormal time-to-event model.

Why lognormal?
- positive support (days >= 0)
- simple analytic CDF and quantiles without SciPy
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd

SQRT2 = math.sqrt(2.0)

# Normal quantiles for central 50% window (25% and 75%)
Z25 = -0.6744897501960817
Z75 =  0.6744897501960817

# Horizon mapping in trading days (approx).
CRISIS_H = {
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
    "2y": 504,
    "3y": 756,
    "5y": 1260,
    "10y": 2520,
}
EUPH_H = {
    "1w": 5,
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
    "2y": 504,
}

def _as_date_col(df: pd.DataFrame) -> pd.Series:
    """Accept either `date` or `time` column. Return pd.Timestamp series."""
    if "date" in df.columns:
        s = pd.to_datetime(df["date"], errors="coerce")
    elif "time" in df.columns:
        s = pd.to_datetime(df["time"], errors="coerce")
    else:
        # assume index
        s = pd.to_datetime(df.index, errors="coerce")
    return s

def _erf_vec(x: np.ndarray) -> np.ndarray:
    """Vectorized erf using math.erf (fast enough for ~1e5 calls)."""
    # np.vectorize wraps Python loop; still fine here.
    return np.vectorize(math.erf, otypes=[float])(x)

def _lognormal_cdf(x_days: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """CDF of lognormal at x (x>0)."""
    x = np.maximum(x_days, 1e-12)
    z = (np.log(x) - mu) / (sigma * SQRT2)
    return 0.5 * (1.0 + _erf_vec(z))

def _safe_float(s: pd.Series, default: float = float("nan")) -> np.ndarray:
    out = pd.to_numeric(s, errors="coerce").to_numpy(dtype=float).copy()
    if not np.isfinite(default):
        return out
    out[~np.isfinite(out)] = default
    return out

def _sigma_from_conf(conf: np.ndarray) -> np.ndarray:
    """
    Convert confidence [0,1] to lognormal sigma.
    High confidence -> narrow distribution.
    """
    c = np.clip(conf, 0.0, 1.0)
    return 0.25 + (1.00 * (1.0 - c))  # 0.25..1.25

def _median_days_from_months(months: np.ndarray, *, floor_days: int) -> np.ndarray:
    # trading-day-ish conversion: 21 days per month
    md = months * 21.0
    md = np.where(np.isfinite(md), md, np.nan)
    md = np.maximum(md, float(floor_days))
    return md

def _apply_trigger_boost(median_days: np.ndarray, trigger: np.ndarray, level: np.ndarray, macro_risk: np.ndarray) -> np.ndarray:
    """
    Make ETA sooner when trigger or macro risk is present.
    This doesn't flip the model into 'now' (that is handled by *_now flags),
    but it increases near-term probability.
    """
    trig = (trigger > 0) | (level > 0) | (macro_risk > 0)
    # Pull median closer by up to 70% when trig is ON.
    factor = np.where(trig, 0.35, 1.0)
    return np.maximum(1.0, median_days * factor)

def build_timing_from_fear_euphoria_daily(fe_d: pd.DataFrame) -> pd.DataFrame:
    """
    fe_d: exported fear_euphoria_daily.csv (daily, forward-filled monthly cycle features + triggers)

    Returns DataFrame matching timing_v1_daily.csv schema expected by the frontend patch:
      date, model, status_*, *_now, p_*_within_*, eta_*_median_days, *_mode_start/end, eta_*_median_date
    """
    if fe_d is None or fe_d.empty:
        return pd.DataFrame()

    dt = _as_date_col(fe_d)
    fe_d = fe_d.copy()
    fe_d["__date"] = dt
    fe_d = fe_d.dropna(subset=["__date"]).sort_values("__date")
    fe_d = fe_d.reset_index(drop=True)

    # Inputs
    conf = _safe_float(fe_d.get("confidence", pd.Series([0.3]*len(fe_d))), default=0.3)
    sigma = _sigma_from_conf(conf)

    months_fear = _safe_float(fe_d.get("months_until_fear", pd.Series([np.nan]*len(fe_d))))
    months_euph = _safe_float(fe_d.get("months_until_euphoria", pd.Series([np.nan]*len(fe_d))))

    fear_trigger = _safe_float(fe_d.get("fear_trigger", pd.Series([0]*len(fe_d))), default=0.0)
    euph_trigger = _safe_float(fe_d.get("euphoria_trigger", pd.Series([0]*len(fe_d))), default=0.0)
    fear_level = _safe_float(fe_d.get("fear_level", pd.Series([0]*len(fe_d))), default=0.0)
    euph_level = _safe_float(fe_d.get("euphoria_level", pd.Series([0]*len(fe_d))), default=0.0)
    macro_risk = _safe_float(fe_d.get("macro_risk", pd.Series([0]*len(fe_d))), default=0.0)

    # Define "now" flags (event already active)
    crisis_now = (macro_risk > 0).astype(int)  # DEFCON1/2
    euphoria_now = (euph_trigger > 0).astype(int)

    # Median ETA (days) from cycle months
    # Fear: volatility-peak cycle => crisis risk; allow longer floor (30d)
    med_fear = _median_days_from_months(months_fear, floor_days=30)
    # Euphoria: trough; allow shorter floor (7d) so "near trough" shows up quickly
    med_euph = _median_days_from_months(months_euph, floor_days=7)

    # If months are missing, mark NO_FEATURES
    status_crisis = np.where(np.isfinite(months_fear), "OK", "NO_FEATURES")
    status_euphoria = np.where(np.isfinite(months_euph), "OK", "NO_FEATURES")

    # Trigger/macro boost
    med_fear = _apply_trigger_boost(med_fear, fear_trigger, fear_level, macro_risk)
    med_euph = _apply_trigger_boost(med_euph, euph_trigger, euph_level, 0*macro_risk)

    # Lognormal params
    mu_fear = np.log(np.maximum(med_fear, 1.0))
    mu_euph = np.log(np.maximum(med_euph, 1.0))

    # Probabilities within horizons
    def cdf_at(h: int, mu: np.ndarray, sig: np.ndarray) -> np.ndarray:
        return _lognormal_cdf(np.full_like(mu, float(h)), mu, sig)

    p_crisis = {}
    for k, h in CRISIS_H.items():
        p = cdf_at(h, mu_fear, sigma)
        p = np.where(crisis_now == 1, 1.0, p)
        p = np.where(status_crisis == "NO_FEATURES", np.nan, p)
        p_crisis[k] = np.clip(p, 0.0, 1.0)

    p_euph = {}
    for k, h in EUPH_H.items():
        p = cdf_at(h, mu_euph, sigma)
        p = np.where(euphoria_now == 1, 1.0, p)
        p = np.where(status_euphoria == "NO_FEATURES", np.nan, p)
        p_euph[k] = np.clip(p, 0.0, 1.0)

    # Likely window: central 50% interval [q25, q75] in days
    q25_f = np.exp(mu_fear + sigma * Z25)
    q75_f = np.exp(mu_fear + sigma * Z75)
    q25_e = np.exp(mu_euph + sigma * Z25)
    q75_e = np.exp(mu_euph + sigma * Z75)

    # Clamp and round
    q25_f = np.maximum(0.0, q25_f)
    q75_f = np.maximum(q25_f, q75_f)
    q25_e = np.maximum(0.0, q25_e)
    q75_e = np.maximum(q25_e, q75_e)

    eta_f = np.where(status_crisis == "NO_FEATURES", np.nan, med_fear)
    eta_e = np.where(status_euphoria == "NO_FEATURES", np.nan, med_euph)

    # Build output
    out = pd.DataFrame()
    out["date"] = fe_d["__date"].dt.date.astype(str)
    out["model"] = "timing_v1_fixed"
    out["status_crisis"] = status_crisis
    out["status_euphoria"] = status_euphoria
    out["crisis_now"] = crisis_now
    out["euphoria_now"] = euphoria_now

    # Attach within probs with the exact column names expected by the frontend
    out["p_crisis_within_1m"] = p_crisis["1m"]
    out["p_crisis_within_3m"] = p_crisis["3m"]
    out["p_crisis_within_6m"] = p_crisis["6m"]
    out["p_crisis_within_1y"] = p_crisis["1y"]
    out["p_crisis_within_2y"] = p_crisis["2y"]
    out["p_crisis_within_3y"] = p_crisis["3y"]
    out["p_crisis_within_5y"] = p_crisis["5y"]
    out["p_crisis_within_10y"] = p_crisis["10y"]

    out["p_euphoria_within_1w"] = p_euph["1w"]
    out["p_euphoria_within_1m"] = p_euph["1m"]
    out["p_euphoria_within_3m"] = p_euph["3m"]
    out["p_euphoria_within_6m"] = p_euph["6m"]
    out["p_euphoria_within_1y"] = p_euph["1y"]
    out["p_euphoria_within_2y"] = p_euph["2y"]

    out["eta_crisis_median_days"] = pd.Series(np.where(np.isfinite(eta_f), np.round(eta_f), np.nan))
    out["eta_euphoria_median_days"] = pd.Series(np.where(np.isfinite(eta_e), np.round(eta_e), np.nan))

    # Dates for median and windows
    base = fe_d["__date"].to_numpy(dtype="datetime64[D]")
    def add_days(arr_days: np.ndarray) -> np.ndarray:
        # arr_days can contain nan; replace with 0 for calc, then mask back to None
        mask = np.isfinite(arr_days)
        days = np.where(mask, arr_days, 0.0).astype(int)
        dt2 = base + days.astype('timedelta64[D]')
        # dt2 -> ISO date strings
        di = pd.to_datetime(dt2)
        out_str = di.date.astype(str)
        out_str = np.where(mask, out_str, None)
        return out_str
    out["eta_crisis_median_date"] = add_days(eta_f)
    out["eta_euphoria_median_date"] = add_days(eta_e)
    out["crisis_mode_start"] = add_days(q25_f)
    out["crisis_mode_end"] = add_days(q75_f)
    out["euphoria_mode_start"] = add_days(q25_e)
    out["euphoria_mode_end"] = add_days(q75_e)

    return out


def export_timing_v1_daily(
    *, data_dir: Optional[Path] = None, in_name: str = "fear_euphoria_daily.csv", out_name: str = "timing_v1_daily.csv"
) -> Optional[Path]:
    """Read data/fear_euphoria_daily.csv and emit data/timing_v1_daily.csv."""
    base_dir = Path(__file__).resolve().parent.parent
    data_dir = data_dir or (base_dir / "data")
    in_path = data_dir / in_name
    out_path = data_dir / out_name
    if not in_path.exists():
        return None
    fe = pd.read_csv(in_path)
    out = build_timing_from_fear_euphoria_daily(fe)
    if out is None or out.empty:
        return None
    out.to_csv(out_path, index=False, encoding="utf-8")
    return out_path