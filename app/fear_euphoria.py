from __future__ import annotations

"""Fear / Euphoria program (cycle forecast + confirm triggers).

This module is intentionally lightweight (NumPy/Pandas only).

Concept
-------
1) Forecast window (cycle-based):
   - Use the long-run 8–14y *volatility cycle* phase (vol_wave_10y_phase, amp)
     from `cycles_monthly.csv`.
   - Convert phase + estimated period into "months until next FEAR (vol peak)"
     and "months until next EUPHORIA (vol trough)".
   - Window flags: within 24m / within 36m.

2) Confirm trigger (event-based): only *inside the window*.
   - FEAR trigger: volatility spike + trend break + macro risk (DEFCON2/1)
   - EUPHORIA trigger: very low vol + overextension + momentum deceleration

Outputs
-------
Exporter writes:
  - fear_euphoria_monthly.csv
  - fear_euphoria_daily.csv (monthly forward-filled to daily, plus trigger flags)
"""

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


TWO_PI = float(2.0 * math.pi)


def _circular_distance(a: float, b: float) -> float:
    """Smallest signed distance from angle a to b (both radians)."""
    return float(((a - b + math.pi) % TWO_PI) - math.pi)


def _rolling_phase_fit(periodic_phase: pd.Series, window: int = 120) -> pd.DataFrame:
    """Estimate period and fit quality from a rolling linear fit of unwrapped phase.

    Returns a DataFrame with columns:
      - omega: radians / month
      - period_months
      - r2
    """
    ph = pd.to_numeric(periodic_phase, errors="coerce")
    x = np.arange(window, dtype=float)
    omega = np.full(len(ph), np.nan)
    per = np.full(len(ph), np.nan)
    r2 = np.full(len(ph), np.nan)

    arr = ph.to_numpy(dtype=float)
    for i in range(window - 1, len(arr)):
        seg = arr[i - window + 1 : i + 1]
        if np.isnan(seg).any():
            continue
        uw = np.unwrap(seg)
        # Linear fit: y = a*x + b
        a, b = np.polyfit(x, uw, 1)
        yhat = a * x + b
        ss_res = float(np.sum((uw - yhat) ** 2))
        ss_tot = float(np.sum((uw - float(np.mean(uw))) ** 2))
        r2_i = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else np.nan

        if not np.isfinite(a) or a <= 0:
            continue
        omega[i] = float(a)
        per_i = TWO_PI / float(a)
        per[i] = float(per_i)
        r2[i] = float(r2_i) if np.isfinite(r2_i) else np.nan

    return pd.DataFrame(
        {
            "omega": pd.Series(omega, index=ph.index),
            "period_months": pd.Series(per, index=ph.index),
            "r2": pd.Series(r2, index=ph.index),
        }
    )


def build_fear_euphoria_from_cycles_monthly(
    cycles_monthly: pd.DataFrame,
    *,
    phase_col: str = "vol_wave_10y_phase",
    amp_col: str = "vol_wave_10y_amp",
    fit_window_months: int = 120,
    tol_rad: float = math.pi / 10.0,  # ~18deg window around target phase
) -> pd.DataFrame:
    """Build forecast windows (months-to-fear/euphoria) from cycles_monthly."""
    if cycles_monthly is None or cycles_monthly.empty:
        return pd.DataFrame()

    df = cycles_monthly.copy()
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        df = df.dropna(subset=["time"]).sort_values("time").set_index("time")
    else:
        # assume index
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna(axis=0, how="any", subset=[]).sort_index()

    ph = pd.to_numeric(df.get(phase_col), errors="coerce")
    amp = pd.to_numeric(df.get(amp_col), errors="coerce")
    if ph is None or ph.isna().all():
        return pd.DataFrame()

    fit = _rolling_phase_fit(ph.fillna(method="ffill"), window=fit_window_months)
    omega = fit["omega"]
    period = fit["period_months"]
    r2 = fit["r2"]

    # Convert phase -> months until target phase (forward time).
    # FEAR: vol peak ~ phase 0
    # EUPHORIA: vol trough ~ phase pi
    target_fear = 0.0
    target_euph = math.pi

    # Use omega for forward phase advance; require positive omega.
    omega_pos = omega.where(omega > 0)

    # Forward diff in [0, 2pi)
    diff_fear = ((target_fear - ph) % TWO_PI)
    diff_euph = ((target_euph - ph) % TWO_PI)

    months_fear = diff_fear / omega_pos
    months_euph = diff_euph / omega_pos

    # If we are already close (within tol), treat as "now".
    dist_fear = ph.apply(lambda v: abs(_circular_distance(float(v), target_fear)) if np.isfinite(v) else np.nan)
    dist_euph = ph.apply(lambda v: abs(_circular_distance(float(v), target_euph)) if np.isfinite(v) else np.nan)
    months_fear = months_fear.where(~(dist_fear <= tol_rad), other=0.0)
    months_euph = months_euph.where(~(dist_euph <= tol_rad), other=0.0)

    # Confidence: amplitude strength + phase-fit quality.
    amp_med = amp.rolling(120, min_periods=60).median()
    amp_ratio = amp / amp_med.replace(0.0, np.nan)
    conf_amp = ((amp_ratio - 0.7) / 0.6).clip(lower=0.0, upper=1.0)
    conf_fit = r2.clip(lower=0.0, upper=1.0)

    conf = (0.6 * conf_amp.fillna(0.0) + 0.4 * conf_fit.fillna(0.0)).clip(0.0, 1.0)

    out = pd.DataFrame(index=df.index)
    out["vol_phase"] = ph
    out["vol_amp"] = amp
    out["period_months"] = period
    out["period_years"] = period / 12.0
    out["phase_fit_r2"] = r2
    out["months_until_fear"] = months_fear
    out["months_until_euphoria"] = months_euph
    out["fear_window_24m"] = (months_fear <= 24).astype(int)
    out["fear_window_36m"] = (months_fear <= 36).astype(int)
    out["euphoria_window_24m"] = (months_euph <= 24).astype(int)
    out["euphoria_window_36m"] = (months_euph <= 36).astype(int)
    out["confidence"] = conf
    return out.reset_index().rename(columns={"index": "time"})


def _rolling_z(x: pd.Series, win_short: int, win_long: int) -> pd.Series:
    """Z-score of short-window realized vol vs long-window baseline."""
    mu = x.rolling(win_long, min_periods=max(30, win_long // 3)).mean()
    sd = x.rolling(win_long, min_periods=max(30, win_long // 3)).std(ddof=0)
    z = (x - mu) / sd.replace(0.0, np.nan)
    return z


def compute_daily_triggers(
    daily_close: pd.Series,
    daily_state: pd.Series,
    forecast_daily: pd.DataFrame,
    *,
    sma_days: int = 200,
    vol_days: int = 20,
    vol_long_days: int = 252,
    vol_spike_z: float = 1.0,
    vol_low_z: float = -0.5,
    overext_pct: float = 0.15,
    mom_fast_days: int = 20,
    mom_slow_days: int = 60,
) -> pd.DataFrame:
    """Compute FEAR/EUPHORIA confirm triggers on daily data."""
    s = pd.to_numeric(daily_close, errors="coerce").dropna().sort_index()
    if s.empty:
        return pd.DataFrame(index=forecast_daily.index)

    px = s.reindex(forecast_daily.index).ffill()
    sma = px.rolling(sma_days, min_periods=max(50, sma_days // 3)).mean()
    trend_break = (px < sma)
    overext = (px / sma - 1.0)
    overext_flag = overext > float(overext_pct)

    ret = px.pct_change(fill_method=None)
    vol = ret.rolling(vol_days, min_periods=max(10, vol_days // 2)).std(ddof=0) * math.sqrt(252)
    vol_z = _rolling_z(vol, win_short=vol_days, win_long=vol_long_days)
    vol_spike = vol_z > float(vol_spike_z)
    vol_low = vol_z < float(vol_low_z)

    mom_fast = px.pct_change(mom_fast_days, fill_method=None)
    mom_slow = px.pct_change(mom_slow_days, fill_method=None)
    mom_decel = (mom_slow > 0) & (mom_fast > 0) & (mom_fast < mom_slow)

    st = daily_state.reindex(forecast_daily.index).fillna(method="ffill")
    macro_risk = st.isin(["DEFCON2", "DEFCON1"])

    fear_window = forecast_daily.get("fear_window_36m", 0).astype(bool)
    euph_window = forecast_daily.get("euphoria_window_36m", 0).astype(bool)

    fear_base = fear_window & vol_spike & trend_break & macro_risk
    # Tiered FEAR severity (0-3) only when base trigger is ON.
    # L1: base trigger
    # L2: base + (DEFCON1 or stronger vol spike or closer-to-peak)
    # L3: base + (DEFCON1 and strong vol spike)
    st_u = st.astype(str).str.upper()
    is_def1 = st_u.eq('DEFCON1')
    close_to_peak = pd.to_numeric(forecast_daily.get('months_until_fear'), errors='coerce') <= 12
    strong_vol = vol_z >= (float(vol_spike_z) + 0.5)
    vstrong_vol = vol_z >= (float(vol_spike_z) + 1.0)
    fear_level = (fear_base.astype(int) * 1)
    fear_level = fear_level + (fear_base & (is_def1 | strong_vol | close_to_peak)).astype(int)
    fear_level = fear_level + (fear_base & is_def1 & vstrong_vol).astype(int)
    fear_level = fear_level.clip(lower=0, upper=3)
    fear_trigger = fear_level > 0

    euph_base = euph_window & vol_low & overext_flag & mom_decel
    close_to_trough = pd.to_numeric(forecast_daily.get('months_until_euphoria'), errors='coerce') <= 12
    very_low_vol = vol_z <= (float(vol_low_z) - 0.5)
    strong_overext = overext >= (float(overext_pct) * 1.5)
    euphoria_level = (euph_base.astype(int) * 1)
    euphoria_level = euphoria_level + (euph_base & (very_low_vol | strong_overext | close_to_trough)).astype(int)
    euphoria_level = euphoria_level + (euph_base & very_low_vol & strong_overext).astype(int)
    euphoria_level = euphoria_level.clip(lower=0, upper=3)
    euph_trigger = euphoria_level > 0

    out = pd.DataFrame(index=forecast_daily.index)
    out["nasdaq_close"] = px
    out["nasdaq_sma"] = sma
    out["nasdaq_overext"] = overext
    out["nasdaq_mom_fast"] = mom_fast
    out["nasdaq_mom_slow"] = mom_slow
    out["vol_ann"] = vol
    out["vol_z"] = vol_z
    out["trend_break"] = trend_break.astype(int)
    out["overext_flag"] = overext_flag.astype(int)
    out["mom_decel"] = mom_decel.astype(int)
    out["macro_risk"] = macro_risk.astype(int)
    out["fear_level"] = pd.to_numeric(fear_level, errors='coerce').fillna(0).astype(int)
    out["euphoria_level"] = pd.to_numeric(euphoria_level, errors='coerce').fillna(0).astype(int)
    out["fear_trigger"] = fear_trigger.astype(int)
    out["euphoria_trigger"] = euph_trigger.astype(int)
    return out


@dataclass(frozen=True)
class FearEuphoriaSnapshot:
    as_of_date: str
    months_until_fear: Optional[float]
    months_until_euphoria: Optional[float]
    confidence: Optional[float]
    fear_window_24m: Optional[int]
    fear_window_36m: Optional[int]
    euphoria_window_24m: Optional[int]
    euphoria_window_36m: Optional[int]
    fear_trigger: Optional[int]
    euphoria_trigger: Optional[int]
    fear_level: Optional[int]
    euphoria_level: Optional[int]


def load_fear_euphoria_snapshot(
    as_of: date,
    *,
    data_dir: Optional[Path] = None,
    csv_name: str = "fear_euphoria_daily.csv",
) -> Optional[FearEuphoriaSnapshot]:
    """Load a daily fear/euphoria snapshot for a given date."""
    base_dir = Path(__file__).resolve().parent.parent
    data_dir = data_dir or (base_dir / "data")
    path = data_dir / csv_name
    if not path.exists():
        return None

    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    if df.empty or "date" not in df.columns:
        return None

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    if df.empty:
        return None
    ts = pd.Timestamp(as_of)
    df = df[df["date"] <= ts]
    if df.empty:
        return None
    row = df.iloc[-1]
    return FearEuphoriaSnapshot(
        as_of_date=str(pd.Timestamp(row["date"]).date()),
        months_until_fear=_safe_float(row.get("months_until_fear")),
        months_until_euphoria=_safe_float(row.get("months_until_euphoria")),
        confidence=_safe_float(row.get("confidence")),
        fear_window_24m=_safe_int(row.get("fear_window_24m")),
        fear_window_36m=_safe_int(row.get("fear_window_36m")),
        euphoria_window_24m=_safe_int(row.get("euphoria_window_24m")),
        euphoria_window_36m=_safe_int(row.get("euphoria_window_36m")),
        fear_trigger=_safe_int(row.get("fear_trigger")),
        euphoria_trigger=_safe_int(row.get("euphoria_trigger")),
        fear_level=_safe_int(row.get("fear_level")),
        euphoria_level=_safe_int(row.get("euphoria_level")),
    )


def _safe_float(v) -> Optional[float]:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)) or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _safe_int(v) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except Exception:
        return None
