from __future__ import annotations

"""Cycle indicators (price & volatility) for NASDAQ.

This module builds *cycle features* from long-run NASDAQ price history.

Important notes
---------------
* These indicators are **decision support** (risk/phase context), not point forecasts.
* We avoid heavy dependencies (no SciPy). Implementation uses NumPy/Pandas.
* Cycle phase is estimated from a trailing window via FFT band-pass + Hilbert transform.
  This is still a smoothed indicator; do not treat it as a strict trading signal.
"""

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd


# -----------------------------------------------------------------------------
# Core helpers
# -----------------------------------------------------------------------------


def _to_monthly_last_close(daily: pd.Series) -> pd.Series:
    """Convert a daily price series to month-end (last available) prices."""
    if daily is None or daily.empty:
        return pd.Series(dtype="float64")
    s = pd.to_numeric(daily, errors="coerce")
    s = s.dropna()
    if s.empty:
        return pd.Series(dtype="float64")
    s = s.sort_index()
    return s.resample("ME").last().dropna()


def _rolling_zscore(x: pd.Series, window: int) -> pd.Series:
    mu = x.rolling(window, min_periods=max(10, window // 3)).mean()
    sd = x.rolling(window, min_periods=max(10, window // 3)).std(ddof=0)
    z = (x - mu) / sd.replace(0.0, np.nan)
    return z


def _bandpass_fft(x: np.ndarray, min_period: float, max_period: float) -> np.ndarray:
    """Simple FFT band-pass filter.

    Parameters
    ----------
    x : array
        Input signal (1D). Assumed evenly sampled.
    min_period, max_period : float
        Period bounds in *samples* (e.g. months). Keep frequencies between
        1/max_period and 1/min_period.

    Returns
    -------
    y : array
        Filtered signal in the same length as x.
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 8:
        return np.full(n, np.nan)

    x = x - np.nanmean(x)
    # Replace remaining NaNs with 0 to keep FFT stable.
    x = np.nan_to_num(x)

    X = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, d=1.0)  # cycles per sample

    f_low = 1.0 / float(max_period)
    f_high = 1.0 / float(min_period)
    mask = (freqs >= f_low) & (freqs <= f_high)
    mask[0] = False  # drop DC

    Xf = np.zeros_like(X)
    Xf[mask] = X[mask]
    y = np.fft.irfft(Xf, n=n)
    return y


def _analytic_signal(x: np.ndarray) -> np.ndarray:
    """Compute analytic signal via an FFT-based Hilbert transform (no SciPy)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    X = np.fft.fft(np.nan_to_num(x))
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = 1
        h[n // 2] = 1
        h[1 : n // 2] = 2
    else:
        h[0] = 1
        h[1 : (n + 1) // 2] = 2
    return np.fft.ifft(X * h)


def _rolling_bandpass_phase(
    s: pd.Series,
    window: int,
    min_period: float,
    max_period: float,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Rolling band-pass component + phase/amplitude (estimated at each timestamp).

    For each time t, uses the trailing `window` samples up to t.
    Outputs are aligned with the original index; the first (window-1) values are NaN.
    """
    s = pd.to_numeric(s, errors="coerce")
    x = s.to_numpy(dtype=float)
    n = len(x)
    comp = np.full(n, np.nan)
    phase = np.full(n, np.nan)
    amp = np.full(n, np.nan)
    if n < window:
        return pd.Series(comp, index=s.index), pd.Series(phase, index=s.index), pd.Series(amp, index=s.index)

    for i in range(window - 1, n):
        seg = x[i - window + 1 : i + 1]
        if np.isnan(seg).all():
            continue
        y = _bandpass_fft(seg, min_period=min_period, max_period=max_period)
        z = _analytic_signal(y)
        comp[i] = float(y[-1])
        amp[i] = float(np.abs(z[-1]))
        phase[i] = float(np.angle(z[-1]))  # [-pi, pi]

    return (
        pd.Series(comp, index=s.index),
        pd.Series(phase, index=s.index),
        pd.Series(amp, index=s.index),
    )


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CycleSnapshot:
    as_of_date: str
    risk_multiplier: float
    price_cycle_z: Optional[float]
    vol_z: Optional[float]
    wave_3y: Optional[float]
    wave_3y_phase: Optional[float]
    wave_7y: Optional[float]
    wave_7y_phase: Optional[float]
    vol_wave_10y: Optional[float]
    vol_wave_10y_phase: Optional[float]


def build_cycles_from_nasdaq_daily(
    nasdaq_daily: pd.Series,
    *,
    price_trend_months: int = 120,
    vol_smooth_months: int = 12,
    phase_window_months: int = 240,
    phase_window_short_months: int = 120,
) -> pd.DataFrame:
    """Return a monthly DataFrame with cycle indicators.

    Columns (monthly index):
      - close
      - logp
      - price_cycle (logp - rolling trend)
      - price_cycle_z (rolling z-score)
      - vol_1m (|monthly log return|)
      - vol_12m (rolling mean)
      - vol_z (rolling z-score on vol_12m)
      - wave_7y (band-pass 5–9y on price_cycle)
      - wave_7y_phase, wave_7y_amp
      - vol_wave_10y (band-pass 8–14y on vol_12m)
      - vol_wave_10y_phase, vol_wave_10y_amp
      - risk_multiplier (heuristic)
    """
    mclose = _to_monthly_last_close(nasdaq_daily)
    if mclose.empty:
        return pd.DataFrame()

    df = pd.DataFrame({"close": mclose})
    df["logp"] = np.log(df["close"].astype(float))
    df["logret"] = df["logp"].diff()
    df["vol_1m"] = df["logret"].abs()

    # Causal trend removal (no centered filter): trailing 10y mean on log-price.
    df["trend_logp"] = df["logp"].rolling(price_trend_months, min_periods=price_trend_months // 2).mean()
    df["price_cycle"] = df["logp"] - df["trend_logp"]
    df["price_cycle_z"] = _rolling_zscore(df["price_cycle"], window=price_trend_months)

    # Volatility context
    df["vol_12m"] = df["vol_1m"].rolling(vol_smooth_months, min_periods=vol_smooth_months).mean()
    df["vol_z"] = _rolling_zscore(df["vol_12m"], window=price_trend_months)

    # Medium/long waves and phases (rolling FFT band-pass + Hilbert)
    # Short/medium wave (~3y) on price_cycle (2–4y band)
    wave_3y, wave_3y_phase, wave_3y_amp = _rolling_bandpass_phase(
        df["price_cycle"].ffill(),
        window=phase_window_short_months,
        min_period=12 * 2.0,
        max_period=12 * 4.0,
    )
    df["wave_3y"] = wave_3y
    df["wave_3y_phase"] = wave_3y_phase
    df["wave_3y_amp"] = wave_3y_amp

    wave_7y, wave_7y_phase, wave_7y_amp = _rolling_bandpass_phase(
        df["price_cycle"].ffill(),
        window=phase_window_months,
        min_period=12 * 5.0,
        max_period=12 * 9.0,
    )
    df["wave_7y"] = wave_7y
    df["wave_7y_phase"] = wave_7y_phase
    df["wave_7y_amp"] = wave_7y_amp

    vol_wave_10y, vol_wave_10y_phase, vol_wave_10y_amp = _rolling_bandpass_phase(
        df["vol_12m"].ffill(),
        window=phase_window_months,
        min_period=12 * 8.0,
        max_period=12 * 14.0,
    )
    df["vol_wave_10y"] = vol_wave_10y
    df["vol_wave_10y_phase"] = vol_wave_10y_phase
    df["vol_wave_10y_amp"] = vol_wave_10y_amp

    # Risk multiplier heuristic
    df["risk_multiplier"] = _compute_risk_multiplier(df)
    return df


def _compute_risk_multiplier(df: pd.DataFrame) -> pd.Series:
    """Translate cycle/vol context into a risk multiplier (0.6..1.15-ish).

    Rules (intentionally simple):
      - When *smoothed volatility* (vol_z) is high -> trim risk.
      - When price cycle is above 0 and rolling over (wave_7y falling) -> slight trim.
      - When price cycle is below 0 and recovering (wave_7y rising) -> slight add.
    """
    vol_z = pd.to_numeric(df.get("vol_z"), errors="coerce")
    wave = pd.to_numeric(df.get("wave_7y"), errors="coerce")
    wave_mom = wave.diff(1)

    mult = pd.Series(1.0, index=df.index, dtype="float64")

    # Volatility regime adjustments
    mult = mult.where(~(vol_z > 1.0), other=mult * 0.75)
    mult = mult.where(~((vol_z > 0.5) & (vol_z <= 1.0)), other=mult * 0.85)
    mult = mult.where(~(vol_z < -0.5), other=mult * 1.05)

    # Price wave context (small nudges)
    mult = mult.where(~((wave > 0) & (wave_mom < 0)), other=mult * 0.90)
    mult = mult.where(~((wave < 0) & (wave_mom > 0)), other=mult * 1.05)

    # Clamp
    return mult.clip(lower=0.60, upper=1.15)


def load_cycle_snapshot(
    as_of: date,
    *,
    data_dir: Optional[Path] = None,
    csv_name: str = "cycles_daily.csv",
) -> Optional[CycleSnapshot]:
    """Load the cycle snapshot for a given date from exported daily cycles CSV."""
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
    return CycleSnapshot(
        as_of_date=str(pd.Timestamp(row["date"]).date()),
        risk_multiplier=float(row.get("risk_multiplier", 1.0) or 1.0),
        price_cycle_z=_safe_float(row.get("price_cycle_z")),
        vol_z=_safe_float(row.get("vol_z")),
        wave_3y=_safe_float(row.get("wave_3y")),
        wave_3y_phase=_safe_float(row.get("wave_3y_phase")),
        wave_7y=_safe_float(row.get("wave_7y")),
        wave_7y_phase=_safe_float(row.get("wave_7y_phase")),
        vol_wave_10y=_safe_float(row.get("vol_wave_10y")),
        vol_wave_10y_phase=_safe_float(row.get("vol_wave_10y_phase")),
    )


def _safe_float(v) -> Optional[float]:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return float(v)
    except Exception:
        return None
