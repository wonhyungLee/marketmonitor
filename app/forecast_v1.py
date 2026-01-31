from __future__ import annotations

"""Forecast Engine v1 (cycle-only, multi-series) with Crisis + Euphoria.

This module builds a lightweight probabilistic forecasting engine using only
cycle-like features extracted from raw market/macro series.

It estimates, for each trading day, the probability that *H days ahead* the
environment will be:

* **Crisis (risk-off)**: macro state is DEFCON2 or DEFCON1.
* **Euphoria (overheat risk-on)**: a high-optimism / overheat regime defined
  by a simple rule on cycle features.

Design goals
------------
* Minimal deps (NumPy/Pandas only; no SciPy / scikit-learn).
* Avoid look-ahead: train only on dates where future labels already exist.
* Robust to missing data (median imputation + z-scoring).

Model
-----
Regularized logistic regression trained with IRLS (iteratively reweighted least
squares) per horizon.

Notes
-----
* "Crisis" labels come from the project's daily state engine.
* "Euphoria" labels are rule-based (see `_compute_euphoria_now`).
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------


DEFAULT_SERIES: List[str] = [
    # Strong cycle-like + predictive signal set (keep small for stability)
    "COPPER_GOLD_RATIO",
    "US10Y",
    "BAMLH0A0HYM2",
    "T10Y2Y",
    "NASDAQ_DLY_IXIC",
    # Optional helpers (may be missing)
    "US02Y",
    "UMCSENT",
    "SAHMREALTIME",
    "WEI",
    "XAUUSD",
]


LOG_SERIES = {
    "NASDAQ_DLY_IXIC",
    "COPPER",
    "XAUUSD",
    "BTCUSD",
    "REMX",
    "ALUMINUM",
    "URANIUM",
    "USDKRW",
}


# Euphoria rule thresholds (chosen to yield ~10–20% positive rate historically)
EUPHORIA_RULE = {
    "NASDAQ_DLY_IXIC__cycle_z": 0.8,     # overheat
    "BAMLH0A0HYM2__cycle_z": -0.2,       # tight credit spreads
    "COPPER_GOLD_RATIO__cycle_z": 0.2,   # growth/optimism
    "NASDAQ_DLY_IXIC__vol_z": 0.2,       # low-ish vol
}


# ----------------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------------


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
    long_win: int = 252 * 3,  # ~3y (trading days)
    slope_win: int = 21,  # ~1m
    vol_win: int = 63,  # ~3m
) -> pd.DataFrame:
    """Cycle-ish feature factory.

    For each raw series, build:
      - __cycle_z : detrended z-score on a long window
      - __slope1m : 1-month change in cycle_z
      - __vol_z   : long-window z-score of short-term diff volatility
    """
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


def _auc(y_true: np.ndarray, y_prob: np.ndarray) -> Optional[float]:
    """Fast AUC (no sklearn). Returns None when undefined."""
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)
    mask = np.isfinite(y_prob)
    y_true = y_true[mask]
    y_prob = y_prob[mask]
    if y_true.size < 10:
        return None
    n_pos = int(np.sum(y_true == 1))
    n_neg = int(np.sum(y_true == 0))
    if n_pos == 0 or n_neg == 0:
        return None

    if np.unique(y_prob).size < 2:
        return 0.5

    order = np.argsort(y_prob)
    ranks = np.empty_like(order)
    ranks[order] = np.arange(order.size)
    sum_ranks_pos = float(np.sum(ranks[y_true == 1]))
    # Mann–Whitney U
    auc = (sum_ranks_pos - n_pos * (n_pos - 1) / 2) / (n_pos * n_neg)
    return float(auc)


def _fit_logistic_irls(
    X: np.ndarray,
    y: np.ndarray,
    *,
    l2: float = 1.0,
    max_iter: int = 50,
    tol: float = 1e-6,
) -> Tuple[np.ndarray, float]:
    """Regularized logistic regression via IRLS.

    X must already be imputed + standardized.
    Returns (coef, intercept).
    """
    n, p = X.shape
    if n == 0 or p == 0:
        return np.zeros(p), 0.0

    # init intercept from class balance
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
    features: List[str]
    coef: np.ndarray
    intercept: float
    medians: np.ndarray
    means: np.ndarray
    stds: np.ndarray
    l2: float
    auc_val: Optional[float] = None


def _prep_X(frame: pd.DataFrame, feature_cols: List[str]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    X = frame[feature_cols].to_numpy(dtype=float)
    medians = np.nanmedian(X, axis=0)
    X_imp = np.where(np.isfinite(X), X, medians)
    means = X_imp.mean(axis=0)
    stds = X_imp.std(axis=0)
    stds = np.where(stds > 1e-12, stds, 1.0)
    X_std = (X_imp - means) / stds
    return X_std, medians, means, stds


def _train_models_for_target(
    frame: pd.DataFrame,
    *,
    feature_cols: List[str],
    horizons: Sequence[int],
    target_base: pd.Series,
    l2: float = 1.0,
    val_rows: int = 252 * 5,
) -> Dict[int, LogisticModel]:
    """Train per-horizon logistic models for a given target series.

    target_base must align with frame rows (same length/index) and represent the
    *current-day* label. Each horizon model predicts target_base shifted by -h.
    """
    models: Dict[int, LogisticModel] = {}
    X_std, medians, means, stds = _prep_X(frame, feature_cols)

    for h in horizons:
        y = target_base.shift(-int(h)).to_numpy(dtype=float)
        y = np.where(np.isfinite(y), y, np.nan)

        # Train only where label exists.
        train_mask = np.isfinite(y)
        if int(np.sum(train_mask)) < 300:
            continue
        y_train = y[train_mask].astype(int)
        X_train = X_std[train_mask]

        coef, intercept = _fit_logistic_irls(X_train, y_train, l2=l2)
        m = LogisticModel(
            features=feature_cols,
            coef=coef,
            intercept=float(intercept),
            medians=medians,
            means=means,
            stds=stds,
            l2=float(l2),
        )

        # Time-aware validation: last N rows of training window.
        idx_train = np.where(train_mask)[0]
        if idx_train.size > 500:
            val_idx = idx_train[-min(int(val_rows), idx_train.size) :]
            y_val = y[val_idx].astype(int)
            p_val = _sigmoid(X_std[val_idx] @ coef + intercept)
            m.auc_val = _auc(y_val, p_val)

        models[int(h)] = m

    return models


def predict(frame: pd.DataFrame, model: LogisticModel) -> np.ndarray:
    X = frame[model.features].to_numpy(dtype=float)
    X_imp = np.where(np.isfinite(X), X, model.medians)
    X_std = (X_imp - model.means) / model.stds
    return _sigmoid(X_std @ model.coef + model.intercept)


def _compute_euphoria_now(frame: pd.DataFrame) -> pd.Series:
    """Rule-based euphoria label on the *current* day.

    Returns a 0/1 series aligned to `frame`.
    """
    def _get(col: str) -> pd.Series:
        if col not in frame.columns:
            return pd.Series(np.nan, index=frame.index)
        return pd.to_numeric(frame[col], errors="coerce")

    nas_cz = _get("NASDAQ_DLY_IXIC__cycle_z")
    baml_cz = _get("BAMLH0A0HYM2__cycle_z")
    cg_cz = _get("COPPER_GOLD_RATIO__cycle_z")
    nas_vz = _get("NASDAQ_DLY_IXIC__vol_z")

    cond = (
        (nas_cz >= float(EUPHORIA_RULE["NASDAQ_DLY_IXIC__cycle_z"]))
        & (baml_cz <= float(EUPHORIA_RULE["BAMLH0A0HYM2__cycle_z"]))
        & (cg_cz >= float(EUPHORIA_RULE["COPPER_GOLD_RATIO__cycle_z"]))
        & (nas_vz <= float(EUPHORIA_RULE["NASDAQ_DLY_IXIC__vol_z"]))
    )
    return cond.astype(int)


def build_forecast_v1_daily(
    asset_universe: pd.DataFrame,
    market_states: pd.DataFrame,
    *,
    series: Sequence[str] = tuple(DEFAULT_SERIES),
    horizons: Sequence[int] = (252, 504, 756),
    l2: float = 1.0,
) -> Tuple[pd.DataFrame, Dict[str, Dict[int, LogisticModel]]]:
    """Build forecast probabilities aligned to trading-day rows.

    Returns (forecast_df, models_by_target).

    Output columns:
      - date
      - p_crisis_1y/2y/3y
      - p_euphoria_1y/2y/3y
      - net_1y/2y/3y (p_euphoria - p_crisis)
      - conf_* and auc_val_* per target/horizon
    """
    px = asset_universe.copy()
    if "date" not in px.columns:
        raise ValueError("asset_universe must have a 'date' column")
    px["date"] = pd.to_datetime(px["date"], errors="coerce")
    px = px.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    st = market_states.copy()
    if "as_of_date" in st.columns and "date" not in st.columns:
        st = st.rename(columns={"as_of_date": "date"})
    st["date"] = pd.to_datetime(st["date"], errors="coerce")
    st = st.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if "state" not in st.columns:
        raise ValueError("market_states must have a 'state' column")

    merged = px.merge(st[["date", "state"]], on="date", how="inner")
    merged = merged.sort_values("date").reset_index(drop=True)
    if merged.empty:
        return pd.DataFrame(columns=["date"]), {}

    use_series = [s for s in series if s in merged.columns]
    feats = _build_cycle_features(merged.set_index("date"), use_series)
    feats = feats.reset_index().rename(columns={"index": "date"})

    frame = merged[["date", "state"]].merge(feats, on="date", how="left")
    frame = frame.sort_values("date").reset_index(drop=True)

    feature_cols = [c for c in frame.columns if c.endswith("__cycle_z") or c.endswith("__slope1m") or c.endswith("__vol_z")]
    feature_cols = sorted(feature_cols)

    # Targets (current day)
    crisis_now = frame["state"].isin(["DEFCON2", "DEFCON1"]).astype(int)
    euphoria_now = _compute_euphoria_now(frame)

    # Check sufficiency
    if not feature_cols:
        # No features => return NaN frame with status
        out = pd.DataFrame({"date": frame["date"].dt.date.astype(str)})
        out["model"] = "forecast_v1"
        out["status"] = "NO_FEATURES"
        out["euphoria_rule"] = ""
        # Create cols with NaNs
        for h in horizons:
            lab = _label(h)
            out[f"p_crisis_{lab}"] = np.nan
            out[f"conf_crisis_{lab}"] = np.nan
            out[f"p_euphoria_{lab}"] = np.nan
            out[f"conf_euphoria_{lab}"] = np.nan
            out[f"net_{lab}"] = np.nan
        return out, {}

    crisis_models = _train_models_for_target(
        frame,
        feature_cols=feature_cols,
        horizons=horizons,
        target_base=crisis_now,
        l2=l2,
    )
    euph_models = _train_models_for_target(
        frame,
        feature_cols=feature_cols,
        horizons=horizons,
        target_base=euphoria_now,
        l2=l2,
    )

    out = pd.DataFrame({"date": frame["date"].dt.date.astype(str)})

    # Determine status based on model availability
    # If we have models for the shortest horizon (1y=252), we call it OK, else WARMUP?
    has_models = (252 in crisis_models) or (252 in euph_models)
    out["status"] = "OK" if has_models else "WARMUP"

    def _label(h: int) -> str:
        return "1y" if h == 252 else "2y" if h == 504 else "3y" if h == 756 else f"{h}d"

    # Crisis
    for h in horizons:
        lab = _label(h)
        m = crisis_models.get(int(h))
        p = np.nan
        conf = None
        auc_val = None
        
        if m is not None:
             p = predict(frame, m)
             # If model is effectively an intercept-only model (no features used or all zeroed), p might be constant.
             # We let it pass, but status might indicate weakness.
             if m.auc_val is not None:
                conf = max(0.0, min(1.0, (m.auc_val - 0.5) / 0.5))
                auc_val = m.auc_val
        
        out[f"p_crisis_{lab}"] = p
        out[f"conf_crisis_{lab}"] = conf
        out[f"auc_val_crisis_{lab}"] = auc_val

    # Euphoria
    for h in horizons:
        lab = _label(h)
        m = euph_models.get(int(h))
        p = np.nan
        conf = None
        auc_val = None
        
        if m is not None:
             p = predict(frame, m)
             if m.auc_val is not None:
                conf = max(0.0, min(1.0, (m.auc_val - 0.5) / 0.5))
                auc_val = m.auc_val

        out[f"p_euphoria_{lab}"] = p
        out[f"conf_euphoria_{lab}"] = conf
        out[f"auc_val_euphoria_{lab}"] = auc_val

    # Net (only where both exist)
    for h in horizons:
        lab = _label(int(h))
        c = out.get(f"p_crisis_{lab}")
        e = out.get(f"p_euphoria_{lab}")
        # Pandas series subtraction handles NaNs correctly (propagates NaN)
        # But we need to be careful if columns are missing (get returns None)
        if c is not None and e is not None:
            out[f"net_{lab}"] = e - c

    out["model"] = "forecast_v1"
    out["euphoria_rule"] = "nas_cz>=0.8 & baml_cz<=-0.2 & cg_cz>=0.2 & nas_vol_z<=0.2"
    return out, {"crisis": crisis_models, "euphoria": euph_models}
