from __future__ import annotations

"""Timing Engine v1: *When* will Crisis / Euphoria start?

This engine complements forecast_v1 ("H years ahead") by estimating
*when* an event is likely to *begin*.

Approach (v1)
-------------
We train a family of regularized logistic models for cumulative timing targets:

  y_H(t) = 1 if the next *enter* event occurs within the next H trading days
           (t+1 .. t+H), conditional on not already being in the event at t.

This yields cumulative probabilities P(T <= H). From these, we derive:
  - Median ETA (approx.): earliest horizon where P(T<=H) >= 0.5
  - Mode window (approx.): horizon interval with the largest incremental mass

Design goals
------------
* Minimal dependencies (NumPy/Pandas only; mirrors forecast_v1 style)
* No silent "0.5" fallbacks: missing features/training -> NaN + status
* Conservative euphoria probabilities (stronger regularization + temperature)

Limitations
-----------
This is an approximate timing model (cumulative classification, not full hazard).
It is intentionally simple for robustness and easy maintenance.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.forecast_v1 import DEFAULT_SERIES, LOG_SERIES, EUPHORIA_RULE


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Trading-day horizons used for cumulative timing targets.
CRISIS_HORIZONS: Dict[str, int] = {
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
    "2y": 252 * 2,
    "3y": 252 * 3,
    "5y": 252 * 5,
    "10y": 252 * 10,
}

EUPHORIA_HORIZONS: Dict[str, int] = {
    "1w": 5,
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
    "2y": 252 * 2,
}

MIN_TRAIN_ROWS = 600  # below this: WARMUP

# Stronger regularization to avoid 0/1 saturation for euphoria.
L2_CRISIS = 1.0
L2_EUPHORIA = 10.0
# Temperature scaling for euphoria (soften logits; >1 reduces confidence)
TEMP_EUPHORIA = 2.0


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _safe_log(s: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce").astype(float)
    out = np.full(len(x), np.nan, dtype=float)
    mask = np.isfinite(x) & (x > 0)
    out[mask] = np.log(x.to_numpy()[mask])
    return pd.Series(out, index=s.index)


def _zscore_trailing(x: pd.Series, win: int) -> pd.Series:
    mu = x.rolling(win, min_periods=max(10, win // 3)).mean()
    sd = x.rolling(win, min_periods=max(10, win // 3)).std(ddof=0)
    return (x - mu) / sd.replace(0.0, np.nan)


def _build_cycle_features(
    df: pd.DataFrame,
    series: Sequence[str],
    *,
    long_win: int = 252 * 3,
    slope_win: int = 21,
    vol_win: int = 63,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for sid in series:
        if sid not in df.columns:
            continue
        x0 = df[sid]
        x = _safe_log(x0) if sid in LOG_SERIES else pd.to_numeric(x0, errors="coerce")
        x = x.astype(float).ffill()

        trend = x.rolling(long_win, min_periods=max(20, long_win // 2)).mean()
        resid = x - trend
        resid_sd = resid.rolling(long_win, min_periods=max(20, long_win // 2)).std(ddof=0)
        cycle_z = resid / resid_sd.replace(0.0, np.nan)

        slope1m = cycle_z.diff(slope_win)

        dx = x.diff()
        vol = dx.rolling(vol_win, min_periods=max(10, vol_win // 2)).std(ddof=0)
        vol_z = _zscore_trailing(vol, long_win)

        out[f"{sid}__cycle_z"] = cycle_z
        out[f"{sid}__slope1m"] = slope1m
        out[f"{sid}__vol_z"] = vol_z

    return out


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -35, 35)
    return 1.0 / (1.0 + np.exp(-z))


def _fit_logistic_irls(
    X: np.ndarray,
    y: np.ndarray,
    *,
    l2: float = 1.0,
    max_iter: int = 50,
    tol: float = 1e-6,
) -> Tuple[np.ndarray, float]:
    n, p = X.shape
    if n == 0 or p == 0:
        return np.zeros(p), 0.0

    pos = float(np.sum(y == 1))
    neg = float(np.sum(y == 0))
    p0 = (pos + 1.0) / (pos + neg + 2.0)
    b = float(np.log(p0 / (1.0 - p0)))
    w = np.zeros(p)
    I = np.eye(p)

    last_ll = None
    for _ in range(max_iter):
        eta = X @ w + b
        p_hat = _sigmoid(eta)

        W = p_hat * (1.0 - p_hat)
        W = np.clip(W, 1e-9, None)
        z = eta + (y - p_hat) / (p_hat * (1.0 - p_hat) + 1e-12)

        Xw = X * np.sqrt(W)[:, None]
        zw = z * np.sqrt(W)

        wsum = float(np.sum(W))
        if wsum <= 0:
            break

        # Center to separate intercept
        X_mean = np.sum(Xw, axis=0) / np.sqrt(wsum)
        z_mean = np.sum(zw) / np.sqrt(wsum)

        Xc = Xw - (np.sqrt(W)[:, None] * (X_mean / np.sqrt(wsum)))
        zc = zw - (np.sqrt(W) * (z_mean / np.sqrt(wsum)))

        A = Xc.T @ Xc + l2 * I
        rhs = Xc.T @ zc
        try:
            w_new = np.linalg.solve(A, rhs)
        except np.linalg.LinAlgError:
            w_new = np.linalg.lstsq(A, rhs, rcond=None)[0]

        b_new = (np.sum(W * (z - X @ w_new)) / np.sum(W)).item()

        dw = np.max(np.abs(w_new - w))
        db = abs(b_new - b)
        w, b = w_new, float(b_new)

        ll = float(np.sum(y * np.log(p_hat + 1e-12) + (1 - y) * np.log(1 - p_hat + 1e-12)))
        ll -= 0.5 * l2 * float(np.sum(w * w))
        if last_ll is not None and abs(ll - last_ll) < tol:
            break
        last_ll = ll

        if max(dw, db) < 1e-5:
            break

    return w, b


@dataclass
class LogisticModel:
    feature_cols: List[str]
    coef: np.ndarray
    intercept: float
    medians: np.ndarray
    means: np.ndarray
    stds: np.ndarray
    l2: float


def _prep_X(frame: pd.DataFrame, feature_cols: List[str]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    X = frame[feature_cols].to_numpy(dtype=float)
    medians = np.nanmedian(X, axis=0)
    X_imp = np.where(np.isfinite(X), X, medians)
    means = X_imp.mean(axis=0)
    stds = X_imp.std(axis=0)
    stds = np.where(stds > 1e-12, stds, 1.0)
    X_std = (X_imp - means) / stds
    return X_std, medians, means, stds


def _predict(model: LogisticModel, frame: pd.DataFrame, *, temperature: float = 1.0) -> np.ndarray:
    X = frame[model.feature_cols].to_numpy(dtype=float)
    X_imp = np.where(np.isfinite(X), X, model.medians)
    X_std = (X_imp - model.means) / model.stds
    eta = (X_std @ model.coef + model.intercept) / max(1e-9, float(temperature))
    return _sigmoid(eta)


def _compute_euphoria_now(feat: pd.DataFrame) -> pd.Series:
    # 1 if all rule thresholds satisfied; else 0. Missing -> 0.
    ok = pd.Series(True, index=feat.index)
    for col, thr in EUPHORIA_RULE.items():
        if col not in feat.columns:
            ok &= False
            continue
        ok &= (feat[col].astype(float) >= float(thr))
    return ok.fillna(False).astype(int)


def _enter_event(now: pd.Series) -> pd.Series:
    prev = now.shift(1).fillna(0).astype(int)
    return ((now.astype(int) == 1) & (prev == 0)).astype(int)


def _within_horizon(enter: np.ndarray, H: int) -> np.ndarray:
    # enter is 0/1 array. y[t]=1 if any enter in next H days.
    n = enter.size
    y = np.zeros(n, dtype=int)
    if n == 0:
        return y
    # Rolling max over forward window using convolution-style trick
    # For robustness, use cumulative sum.
    cs = np.cumsum(enter, dtype=int)
    for i in range(n):
        j = min(n - 1, i + H)
        # next window is (i+1..j)
        lo = i
        hi = j
        s = cs[hi] - (cs[lo] if lo >= 0 else 0)
        if i == 0:
            # cs[hi] - 0 counts [0..hi], but we want [1..hi]
            s = cs[hi] - enter[0]
        else:
            # cs[hi]-cs[i] counts (i+1..hi)
            s = cs[hi] - cs[i]
        y[i] = 1 if s > 0 else 0
    return y


def _derive_eta_and_window(
    date_index: pd.DatetimeIndex,
    p_cum: Dict[str, float],
    horizons: Dict[str, int],
) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Return (median_days, mode_start_date, mode_end_date) for a single row."""
    # Sort horizons by days
    items = sorted(horizons.items(), key=lambda kv: kv[1])
    Hs = [d for _, d in items]
    Ps = [float(p_cum.get(k, np.nan)) for k, _ in items]

    # ensure finite
    if not np.any(np.isfinite(Ps)):
        return None, None, None

    # Make nondecreasing (monotonic) for stability
    Pm = []
    m = -np.inf
    for p in Ps:
        if not np.isfinite(p):
            Pm.append(np.nan)
            continue
        m = max(m, p)
        Pm.append(m)

    # increments
    inc = []
    prev = 0.0
    for p in Pm:
        if not np.isfinite(p):
            inc.append(np.nan)
            continue
        inc.append(max(0.0, p - prev))
        prev = p

    # mode window = max incremental mass
    inc_arr = np.array([v if np.isfinite(v) else -1 for v in inc], dtype=float)
    mode_i = int(np.argmax(inc_arr)) if inc_arr.size else 0

    start_days = 1 if mode_i == 0 else (Hs[mode_i - 1] + 1)
    end_days = Hs[mode_i]

    # median ETA: find first horizon crossing 0.5
    median_days: Optional[int] = None
    if np.isfinite(Pm[-1]) and Pm[-1] >= 0.5:
        prev_h = 0
        prev_p = 0.0
        for h, p in zip(Hs, Pm):
            if not np.isfinite(p):
                continue
            if p >= 0.5:
                # linear interpolate between (prev_h, prev_p) and (h,p)
                if p == prev_p:
                    median_days = int(h)
                else:
                    frac = (0.5 - prev_p) / (p - prev_p)
                    median_days = int(round(prev_h + frac * (h - prev_h)))
                break
            prev_h, prev_p = h, p

    # convert window to dates (based on last date in index, but caller supplies date)
    # Here we return offsets only; caller will convert.
    # We'll return date strings directly for convenience.
    # date_index is 1-length index in our use.
    base = date_index[0]
    mode_start_date = (base + pd.Timedelta(days=int(start_days))).date().isoformat()
    mode_end_date = (base + pd.Timedelta(days=int(end_days))).date().isoformat()

    return median_days, mode_start_date, mode_end_date


def _fit_models_for_event(
    features: pd.DataFrame,
    now: pd.Series,
    horizons: Dict[str, int],
    *,
    l2: float,
) -> Tuple[Dict[str, Optional[LogisticModel]], str]:
    """Fit one cumulative model per horizon. Returns (models, status)."""
    # Candidate feature columns: keep only those referenced by the euphoria rule + core cycles.
    # For stability: use cycle_z + slope + vol for DEFAULT_SERIES
    feature_cols = [c for c in features.columns if any(s in c for s in ("__cycle_z", "__slope1m", "__vol_z"))]
    if not feature_cols:
        return {k: None for k in horizons.keys()}, "NO_FEATURES"

    frame = features.copy()
    frame["now"] = now.astype(int).to_numpy()

    # event enter
    enter = _enter_event(now).to_numpy(dtype=int)

    # Train only on rows where not in event now
    elig = (now.astype(int) == 0)
    frame_elig = frame.loc[elig].copy()
    if frame_elig.empty or len(frame_elig) < MIN_TRAIN_ROWS:
        return {k: None for k in horizons.keys()}, "WARMUP"

    models: Dict[str, Optional[LogisticModel]] = {}

    for key, H in horizons.items():
        y = _within_horizon(enter, H)
        y = pd.Series(y, index=features.index)
        y_elig = y.loc[elig]

        # Need labels available: y_elig is defined for all, but becomes less meaningful near end where
        # future is unknown. We approximate by removing last H days from training.
        if len(y_elig) <= H + 10:
            models[key] = None
            continue
        train_mask = y_elig.index <= y_elig.index[-(H + 1)]
        train = frame_elig.loc[train_mask]
        y_train = y_elig.loc[train_mask].to_numpy(dtype=int)

        if len(train) < MIN_TRAIN_ROWS:
            models[key] = None
            continue

        if np.unique(y_train).size < 2:
            models[key] = None
            continue

        X_std, med, mu, sd = _prep_X(train, feature_cols)
        w, b = _fit_logistic_irls(X_std, y_train, l2=l2)

        models[key] = LogisticModel(
            feature_cols=feature_cols,
            coef=w,
            intercept=b,
            medians=med,
            means=mu,
            stds=sd,
            l2=l2,
        )

    any_model = any(m is not None for m in models.values())
    status = "OK" if any_model else "WARMUP"
    return models, status


def build_timing_v1_daily(
    asset_universe: pd.DataFrame,
    states: pd.DataFrame,
) -> pd.DataFrame:
    """Build daily timing probabilities + ETA windows.

    asset_universe must have columns:
      - date (datetime)
      - raw series columns (e.g., DEFAULT_SERIES)

    states must have columns:
      - date (datetime)
      - state (str)

    Returns a DataFrame with one row per trading day.
    """

    if asset_universe is None or asset_universe.empty:
        return pd.DataFrame(columns=["date", "model", "status_crisis", "status_euphoria"])

    df = asset_universe.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    df = df.set_index("date")

    feat = _build_cycle_features(df, DEFAULT_SERIES)

    # Crisis now from states
    st = states.copy() if states is not None else pd.DataFrame(columns=["date", "state"])
    if not st.empty:
        st["date"] = pd.to_datetime(st["date"], errors="coerce")
        st = st.dropna(subset=["date"]).sort_values("date")
        st = st.set_index("date")
    crisis_now = pd.Series(0, index=feat.index)
    if not st.empty and "state" in st.columns:
        crisis_now = st["state"].reindex(feat.index).ffill().fillna("WARMUP")
        crisis_now = crisis_now.isin(["DEFCON2", "DEFCON1"]).astype(int)

    euphoria_now = _compute_euphoria_now(feat)

    # Fit models
    crisis_models, crisis_status = _fit_models_for_event(feat, crisis_now, CRISIS_HORIZONS, l2=L2_CRISIS)
    euphoria_models, euphoria_status = _fit_models_for_event(feat, euphoria_now, EUPHORIA_HORIZONS, l2=L2_EUPHORIA)

    out = pd.DataFrame(index=feat.index)
    out["date"] = out.index.date.astype(str)
    out["model"] = "timing_v1"
    out["status_crisis"] = crisis_status
    out["status_euphoria"] = euphoria_status
    out["crisis_now"] = crisis_now.astype(int).to_numpy()
    out["euphoria_now"] = euphoria_now.astype(int).to_numpy()

    # Predict cumulative probabilities
    for key, H in CRISIS_HORIZONS.items():
        col = f"p_crisis_within_{key}"
        m = crisis_models.get(key)
        if m is None:
            out[col] = np.nan
        else:
            out[col] = _predict(m, feat)

    for key, H in EUPHORIA_HORIZONS.items():
        col = f"p_euphoria_within_{key}"
        m = euphoria_models.get(key)
        if m is None:
            out[col] = np.nan
        else:
            out[col] = _predict(m, feat, temperature=TEMP_EUPHORIA)

    # Enforce monotonicity across horizons per-row (cumulative max)
    def _monotonic_cols(prefix: str, horizons: Dict[str, int]) -> List[str]:
        keys = [k for k, _ in sorted(horizons.items(), key=lambda kv: kv[1])]
        return [f"{prefix}{k}" for k in keys]

    crisis_cols = _monotonic_cols("p_crisis_within_", CRISIS_HORIZONS)
    euph_cols = _monotonic_cols("p_euphoria_within_", EUPHORIA_HORIZONS)

    out[crisis_cols] = out[crisis_cols].apply(lambda r: pd.Series(np.maximum.accumulate(np.nan_to_num(r.to_numpy(), nan=-np.inf))), axis=1)
    out[euph_cols] = out[euph_cols].apply(lambda r: pd.Series(np.maximum.accumulate(np.nan_to_num(r.to_numpy(), nan=-np.inf))), axis=1)

    # Replace -inf back to NaN
    out[crisis_cols] = out[crisis_cols].replace(-np.inf, np.nan)
    out[euph_cols] = out[euph_cols].replace(-np.inf, np.nan)

    # ETA and mode window per row using the cumulative probabilities
    eta_crisis_days = []
    mode_crisis_start = []
    mode_crisis_end = []
    eta_eup_days = []
    mode_eup_start = []
    mode_eup_end = []

    # Precompute ordered keys
    crisis_items = sorted(CRISIS_HORIZONS.items(), key=lambda kv: kv[1])
    euph_items = sorted(EUPHORIA_HORIZONS.items(), key=lambda kv: kv[1])

    for idx, row in out.iterrows():
        # If already in event, ETA=0 and window=today
        if int(row.get("crisis_now", 0)) == 1:
            eta_crisis_days.append(0)
            mode_crisis_start.append(idx.date().isoformat())
            mode_crisis_end.append(idx.date().isoformat())
        else:
            p_cum = {k: row.get(f"p_crisis_within_{k}") for k, _ in crisis_items}
            md, ms, me = _derive_eta_and_window(pd.DatetimeIndex([idx]), p_cum, CRISIS_HORIZONS)
            eta_crisis_days.append(md)
            mode_crisis_start.append(ms)
            mode_crisis_end.append(me)

        if int(row.get("euphoria_now", 0)) == 1:
            eta_eup_days.append(0)
            mode_eup_start.append(idx.date().isoformat())
            mode_eup_end.append(idx.date().isoformat())
        else:
            p_cum = {k: row.get(f"p_euphoria_within_{k}") for k, _ in euph_items}
            md, ms, me = _derive_eta_and_window(pd.DatetimeIndex([idx]), p_cum, EUPHORIA_HORIZONS)
            eta_eup_days.append(md)
            mode_eup_start.append(ms)
            mode_eup_end.append(me)

    out["eta_crisis_median_days"] = eta_crisis_days
    out["crisis_mode_start"] = mode_crisis_start
    out["crisis_mode_end"] = mode_crisis_end

    out["eta_euphoria_median_days"] = eta_eup_days
    out["euphoria_mode_start"] = mode_eup_start
    out["euphoria_mode_end"] = mode_eup_end

    # Derive median ETA dates (if days is present)
    def _eta_date(base: pd.Timestamp, days: Optional[int]) -> Optional[str]:
        if days is None or (isinstance(days, float) and np.isnan(days)):
            return None
        try:
            return (base + pd.Timedelta(days=int(days))).date().isoformat()
        except Exception:
            return None

    out["eta_crisis_median_date"] = [
        _eta_date(idx, d) for idx, d in zip(out.index, out["eta_crisis_median_days"].tolist())
    ]
    out["eta_euphoria_median_date"] = [
        _eta_date(idx, d) for idx, d in zip(out.index, out["eta_euphoria_median_days"].tolist())
    ]

    out = out.reset_index(drop=True)
    return out
