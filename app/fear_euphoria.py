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
    fear_phase_col: str = "vol_wave_10y_phase",
    fear_amp_col: str = "vol_wave_10y_amp",
    euphoria_phase_col: str = "wave_3y_phase",
    euphoria_amp_col: str = "wave_3y_amp",
    fit_window_fear: int = 120,
    fit_window_euphoria: int = 60,
    tol_rad: float = math.pi / 10,
) -> pd.DataFrame:
    """Create month-level timing features for Fear (crisis) and Euphoria.

    Key idea
      - Fear: driven by *volatility* cycle peaks (vol_wave_10y_phase -> phase 0)
      - Euphoria: driven by *price* cycle peaks (wave_3y_phase -> phase 0)

    Output columns (monthly):
      time,
      months_until_fear, months_until_euphoria,
      fear_window_24m, fear_window_36m,
      euphoria_window_24m, euphoria_window_36m,
      confidence_fear, confidence_euphoria,
      fear_period_months, euphoria_period_months,
      fear_phase, euphoria_phase

    Backward compatibility: also emits `confidence` as the average of both.
    """
    if cycles_monthly is None or len(cycles_monthly) == 0:
        return pd.DataFrame()

    df = cycles_monthly.copy()

    # Accept either index(time) or a 'time' column.
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        df = df.dropna(subset=["time"]).sort_values("time")
        df = df.set_index(df["time"].dt.to_period("M").dt.to_timestamp("M"))
    else:
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna(subset=[df.index.name] if df.index.name else None)
        df = df.sort_index()
        df.index = df.index.to_period("M").to_timestamp("M")

    # Feature columns
    for col in [fear_phase_col, fear_amp_col, euphoria_phase_col, euphoria_amp_col]:
        if col not in df.columns:
            # Missing cycle feature -> cannot compute.
            return pd.DataFrame()

    fear_phase = pd.to_numeric(df[fear_phase_col], errors="coerce")
    fear_amp = pd.to_numeric(df[fear_amp_col], errors="coerce")

    euph_phase = pd.to_numeric(df[euphoria_phase_col], errors="coerce")
    euph_amp = pd.to_numeric(df[euphoria_amp_col], errors="coerce")

    # Helper: compute months_until + confidence from a phase/amp stream.
    def _calc(phase: pd.Series, amp: pd.Series, *, fit_window: int, label: str) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        phase_ff = phase.ffill()
        fit = _rolling_phase_fit(phase_ff, window=fit_window)

        omega = pd.to_numeric(fit.get("omega"), errors="coerce")
        omega_pos = omega.where(omega > 0)

        target = 0.0  # phase=0 means PEAK
        diff = (target - phase_ff) % TWO_PI
        months = diff / omega_pos

        # Treat near-peak as "now".
        dist = phase_ff.apply(lambda v: abs(_circular_distance(float(v), target)) if pd.notna(v) else float('nan'))
        months = months.where(~(dist <= tol_rad), other=0.0)

        # Period in months
        period_months = pd.to_numeric(fit.get("period_months"), errors="coerce")
        r2 = pd.to_numeric(fit.get("r2"), errors="coerce").clip(0, 1)

        # Confidence: combine amplitude stability + fit quality
        amp_w = max(60, int(fit_window * 2))
        amp_med = amp.rolling(amp_w, min_periods=max(30, fit_window // 2)).median()
        amp_ratio = amp / amp_med.replace(0, float('nan'))
        conf_amp = ((amp_ratio - 0.7) / 0.6).clip(0, 1)
        conf = (0.6 * conf_amp.fillna(0) + 0.4 * r2.fillna(0)).clip(0, 1)

        return months, period_months, r2, conf

    months_until_fear, fear_period_months, fear_r2, conf_fear = _calc(
        fear_phase, fear_amp, fit_window=fit_window_fear, label="fear"
    )
    months_until_euph, euph_period_months, euph_r2, conf_euph = _calc(
        euph_phase, euph_amp, fit_window=fit_window_euphoria, label="euphoria"
    )

    # Windows: start tracking "in window" when within 24/36 months of the projected peak.
    fear_window_24m = (months_until_fear <= 24).astype(int)
    fear_window_36m = (months_until_fear <= 36).astype(int)
    euphoria_window_24m = (months_until_euph <= 24).astype(int)
    euphoria_window_36m = (months_until_euph <= 36).astype(int)

    out = pd.DataFrame(index=df.index)
    out["months_until_fear"] = months_until_fear
    out["months_until_euphoria"] = months_until_euph

    out["fear_window_24m"] = fear_window_24m
    out["fear_window_36m"] = fear_window_36m
    out["euphoria_window_24m"] = euphoria_window_24m
    out["euphoria_window_36m"] = euphoria_window_36m

    out["confidence_fear"] = conf_fear
    out["confidence_euphoria"] = conf_euph
    out["confidence"] = ((conf_fear.fillna(0) + conf_euph.fillna(0)) / 2.0).clip(0, 1)

    out["fear_period_months"] = fear_period_months
    out["euphoria_period_months"] = euph_period_months

    out["fear_phase"] = fear_phase
    out["euphoria_phase"] = euph_phase

    out.index.name = "time"
    out = out.reset_index()
    out["time"] = out["time"].dt.date.astype(str)
    return out


def compute_daily_triggers(
    *,
    daily_close: pd.Series,
    daily_state: pd.Series,
    forecast_daily: pd.DataFrame,
    overext_z: float = 1.6,
    mom_fast_days: int = 21,
    mom_slow_days: int = 126,
    vol_days: int = 63,
    vol_high_z: float = 0.75,
    runup_days: int = 252,
    runup_min: float = 0.20,
) -> pd.DataFrame:
    """Compute daily confirm triggers for Fear/Euphoria.

    Notes
    - Fear confirmation is macro-driven: DEFCON1/2 and volatility/trend breakdown.
    - Euphoria confirmation is price-cycle + overextension: near predicted peak on the 3y wave,
      with stretched price vs MA and momentum deceleration.
    """
    if forecast_daily is None or forecast_daily.empty:
        return pd.DataFrame()

    px = pd.to_numeric(daily_close, errors="coerce")
    px = px.dropna()
    if px.empty:
        return pd.DataFrame(index=forecast_daily.index)

    # Align index
    idx = pd.DatetimeIndex(forecast_daily.index)
    px = px.reindex(idx).ffill()

    # 1) Basic features
    sma = px.rolling(200, min_periods=100).mean()
    overext = (px / sma) - 1.0
    overext_zs = (overext - overext.rolling(252, min_periods=126).mean()) / (overext.rolling(252, min_periods=126).std())

    mom_fast = px.pct_change(mom_fast_days)
    mom_slow = px.pct_change(mom_slow_days)
    mom_decel = (mom_fast < 0) & (mom_slow > 0)

    vol = px.pct_change().rolling(vol_days, min_periods=max(10, vol_days // 3)).std() * (252 ** 0.5)
    vol_z = (vol - vol.rolling(252, min_periods=126).mean()) / (vol.rolling(252, min_periods=126).std())

    # Run-up context (12m)
    runup = px.pct_change(runup_days)
    runup_flag = runup > float(runup_min)

    # Trend break: below SMA200
    trend_break = (px < sma)

    # Macro risk from daily_state (DEFCON1/2)
    st = daily_state.copy()
    st.index = pd.to_datetime(st.index, errors="coerce")
    st = st.reindex(idx).ffill()
    macro_risk = st.isin(["DEFCON1", "DEFCON2"]).astype(int)

    # 2) Window flags from forecast (monthly -> daily ffill)
    close_to_peak = pd.to_numeric(forecast_daily.get("months_until_euphoria"), errors="coerce")
    close_to_fear = pd.to_numeric(forecast_daily.get("months_until_fear"), errors="coerce")

    fear_window_24m = pd.to_numeric(forecast_daily.get("fear_window_24m"), errors="coerce").fillna(0).astype(int)
    fear_window_36m = pd.to_numeric(forecast_daily.get("fear_window_36m"), errors="coerce").fillna(0).astype(int)
    euph_window_24m = pd.to_numeric(forecast_daily.get("euphoria_window_24m"), errors="coerce").fillna(0).astype(int)
    euph_window_36m = pd.to_numeric(forecast_daily.get("euphoria_window_36m"), errors="coerce").fillna(0).astype(int)

    # 3) Confirm triggers
    # Fear: macro risk + (trend break OR vol spike)
    fear_trigger = (macro_risk > 0) & (trend_break | (vol_z > 1.0))

    # Euphoria: near predicted peak window + stretched + deceleration + decent run-up, but avoid high-vol panic
    vol_ok = (vol_z <= float(vol_high_z))
    overext_flag = (overext_zs > float(overext_z))

    # Tighten near-term: within 12 months of predicted peak if available
    near_peak = (close_to_peak <= 12.0) if close_to_peak is not None else pd.Series(False, index=idx)

    euph_trigger = (euph_window_36m > 0) & vol_ok & overext_flag & mom_decel & runup_flag
    euph_trigger = euph_trigger | ((euph_window_24m > 0) & vol_ok & overext_flag & mom_decel)
    euph_trigger = euph_trigger | (near_peak & vol_ok & overext_flag & mom_decel)

    # Levels for UI (0..3)
    fear_level = (macro_risk.astype(int) + (trend_break.astype(int)) + ((vol_z > 1.0).astype(int))).clip(0, 3)

    # Euphoria levels: combine distance to peak, overextension, and run-up
    euphoria_level = (
        (near_peak.astype(int))
        + (overext_flag.astype(int))
        + ((runup > 0.35).astype(int))
    ).clip(0, 3)

    out = pd.DataFrame(index=idx)
    out["nasdaq_close"] = px
    out["nasdaq_sma"] = sma
    out["nasdaq_overext"] = overext
    out["nasdaq_mom_fast"] = mom_fast
    out["nasdaq_mom_slow"] = mom_slow
    out["vol_ann"] = vol
    out["vol_z"] = vol_z
    out["runup_12m"] = runup
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
    confidence_fear: Optional[float]
    confidence_euphoria: Optional[float]
    fear_period_months: Optional[float]
    euphoria_period_months: Optional[float]
    fear_phase: Optional[float]
    euphoria_phase: Optional[float]
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
        confidence_fear=_safe_float(row.get("confidence_fear")) or _safe_float(row.get("confidence")),
        confidence_euphoria=_safe_float(row.get("confidence_euphoria")) or _safe_float(row.get("confidence")),
        fear_period_months=_safe_float(row.get("fear_period_months")),
        euphoria_period_months=_safe_float(row.get("euphoria_period_months")),
        fear_phase=_safe_float(row.get("fear_phase")),
        euphoria_phase=_safe_float(row.get("euphoria_phase")),
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
