from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from app import db
from app.cycles import load_cycle_snapshot
from app.fear_euphoria import load_fear_euphoria_snapshot
from app.settings import get_settings


def _read_csv_any_encoding(path: Path) -> pd.DataFrame:
    last_err: Optional[Exception] = None
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as exc:  # pragma: no cover (best-effort)
            last_err = exc
    if last_err:
        raise last_err
    return pd.read_csv(path)


def _sniff_asset_universe_csv(repo_root: Path) -> Optional[Path]:
    """
    Locate the "asset universe" CSV by sniffing headers.

    This avoids hard-coding non-ASCII filenames/paths.
    """
    for p in repo_root.rglob("*.csv"):
        if not p.is_file():
            continue
        try:
            head = p.open("rb").read(4096)
        except Exception:
            continue
        txt = head.decode("utf-8-sig", errors="ignore")
        if not txt.startswith("time"):
            continue
        if "BTCUSD" in txt and "USDKRW" in txt and "XAUUSD" in txt:
            return p
    return None


def _asset_col(df: pd.DataFrame, prefix: str) -> Optional[str]:
    cols = [c for c in df.columns if str(c).startswith(prefix)]
    return cols[0] if cols else None


def _yield_to_bond_index(yield_pct: pd.Series, duration: float, add_carry: bool) -> pd.Series:
    """
    Convert a yield series (% units) into a synthetic bond total return index.

    Approximation:
      r_t ~= carry_t - duration * dY
      carry_t ~= y_{t-1} / 252
      dY is yield change in decimal units (e.g. 0.01 == +1.0%p)
    """
    y = pd.to_numeric(yield_pct, errors="coerce")
    first = y.first_valid_index()
    out = pd.Series(index=y.index, dtype="float64")
    if first is None:
        return out

    y = y.ffill().loc[first:]
    dy = y.diff() / 100.0
    carry = (y.shift(1) / 100.0) / 252.0 if add_carry else 0.0
    r = (carry - float(duration) * dy).fillna(0.0)
    idx = (1.0 + r).cumprod()
    if len(idx):
        idx.iloc[0] = 1.0
    out.loc[first:] = idx
    return out


ASSET_SERIES_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "NASDAQ": ("NASDAQ_DLY_IXIC", "NASDAQ", "IXIC"),
    "GOLD": ("XAUUSD",),
    "BTC": ("BTCUSD",),
    "USDKRW": ("USDKRW",),
    "COPPER": ("COPPER",),
    "REMX": ("REMX",),
    "ALUMINUM": ("ALUMINUM",),
    "URANIUM": ("URANIUM",),
}

BOND_SERIES_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "UST10Y": ("US10Y",),
    "UST2Y": ("US02Y", "US2Y"),
}


def _pick_series_column(columns: List[str], candidates: Tuple[str, ...]) -> Optional[str]:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


@dataclass(frozen=True)
class PortfolioRecommendation:
    as_of_date: str
    portfolio_date: str
    model: str
    target_vol_ann: float
    vol_window_days: int
    ma_window_days: int
    leverage_cap: float
    gross_exposure: float
    cash_weight: float
    weights: Dict[str, float]
    diagnostics: Dict[str, Dict]


@lru_cache(maxsize=1)
def _load_asset_universe_from_csv() -> pd.DataFrame:
    settings = get_settings()
    repo_root = Path(".")
    path: Optional[Path] = None

    cfg = str(getattr(settings, "asset_universe_csv_path", "") or "").strip()
    if cfg:
        p = Path(cfg)
        if p.exists():
            path = p

    if path is None:
        path = _sniff_asset_universe_csv(repo_root)

    if path is None or not path.exists():
        return pd.DataFrame()

    raw = _read_csv_any_encoding(path)
    if "time" not in raw.columns or "close" not in raw.columns:
        return pd.DataFrame()

    raw = raw.copy()
    raw["time"] = pd.to_datetime(raw["time"], errors="coerce")
    raw = raw.dropna(subset=["time"]).sort_values("time").set_index("time")

    # Canonical columns used by the portfolio model.
    colmap: Dict[str, str] = {}
    colmap["NASDAQ"] = "close"

    for asset_id, prefix in (
        ("GOLD", "XAUUSD"),
        ("BTC", "BTCUSD"),
        ("USDKRW", "USDKRW"),
        ("COPPER", "COPPER"),
        ("REMX", "REMX"),
        ("ALUMINUM", "ALUMINUM"),
        ("URANIUM", "URANIUM"),
    ):
        c = _asset_col(raw, prefix)
        if c:
            colmap[asset_id] = c

    px = raw[list(colmap.values())].rename(columns={v: k for k, v in colmap.items()})

    # Bonds: synthesize price indices from yields if present (US10Y / US02Y).
    add_carry = bool(getattr(settings, "portfolio_bond_add_carry", True))
    y10_col = _asset_col(raw, "US10Y")
    y2_col = _asset_col(raw, "US02Y") or _asset_col(raw, "US2Y")
    if y10_col:
        dur10 = float(getattr(settings, "portfolio_bond10y_duration", 8.5))
        px["UST10Y"] = _yield_to_bond_index(raw[y10_col], duration=dur10, add_carry=add_carry)
    if y2_col:
        dur2 = float(getattr(settings, "portfolio_bond2y_duration", 1.9))
        px["UST2Y"] = _yield_to_bond_index(raw[y2_col], duration=dur2, add_carry=add_carry)
    return px


def _load_asset_universe_from_db(conn) -> pd.DataFrame:
    settings = get_settings()
    series_ids = set()
    for candidates in ASSET_SERIES_CANDIDATES.values():
        series_ids.update(candidates)
    for candidates in BOND_SERIES_CANDIDATES.values():
        series_ids.update(candidates)
    rows = db.fetch_observations_for_series(conn, sorted(series_ids), interval="1D")
    if not rows:
        return pd.DataFrame()

    data_frame = pd.DataFrame(rows, columns=["series_id", "time_utc_ms", "interval", "value"])
    if data_frame.empty:
        return data_frame

    data_frame["timestamp"] = pd.to_datetime(data_frame["time_utc_ms"], unit="ms", utc=True)
    data_frame["date"] = data_frame["timestamp"].dt.date
    data_frame = data_frame.sort_values(["series_id", "time_utc_ms"])
    data_frame = data_frame.drop_duplicates(subset=["series_id", "date"], keep="last")

    pivot = data_frame.pivot(index="date", columns="series_id", values="value")
    if pivot.empty:
        return pivot

    pivot = pivot.sort_index()
    pivot.index = pd.to_datetime(pivot.index)

    colmap: Dict[str, str] = {}
    columns = [str(c) for c in pivot.columns]
    for asset_id, candidates in ASSET_SERIES_CANDIDATES.items():
        picked = _pick_series_column(columns, candidates)
        if picked:
            colmap[asset_id] = picked

    if not colmap:
        return pd.DataFrame()

    px = pivot[list(colmap.values())].rename(columns={v: k for k, v in colmap.items()})

    add_carry = bool(getattr(settings, "portfolio_bond_add_carry", True))
    y10_col = _pick_series_column(columns, BOND_SERIES_CANDIDATES["UST10Y"])
    if y10_col:
        dur10 = float(getattr(settings, "portfolio_bond10y_duration", 8.5))
        px["UST10Y"] = _yield_to_bond_index(pivot[y10_col], duration=dur10, add_carry=add_carry)
    y2_col = _pick_series_column(columns, BOND_SERIES_CANDIDATES["UST2Y"])
    if y2_col:
        dur2 = float(getattr(settings, "portfolio_bond2y_duration", 1.9))
        px["UST2Y"] = _yield_to_bond_index(pivot[y2_col], duration=dur2, add_carry=add_carry)

    return px


def load_asset_universe(conn=None) -> pd.DataFrame:
    if conn is not None:
        px = _load_asset_universe_from_db(conn)
        if not px.empty:
            return px
    return _load_asset_universe_from_csv()


def _compute_portfolio_from_px(
    as_of: date, macro_state: str, px: pd.DataFrame, settings
) -> Optional[PortfolioRecommendation]:
    model = str(getattr(settings, "portfolio_allocation_model", "") or "").strip().lower() or "multi_asset_trend_vol"
    if model not in ("multi_asset_trend_vol", "trend_vol", "trend_vol_target"):
        return None

    if px is None or px.empty:
        return None

    # Align to the last available trading day <= as_of.
    as_of_ts = pd.Timestamp(as_of)
    px_upto = px[px.index <= as_of_ts]
    if px_upto.empty:
        return None
    portfolio_ts = px_upto.index[-1]

    ma_win = int(getattr(settings, "portfolio_ma_window", 200))
    vol_win = int(getattr(settings, "portfolio_vol_window_days", 20))
    target_vol = float(getattr(settings, "portfolio_target_vol_ann", 0.35))
    cap = float(getattr(settings, "portfolio_leverage_cap", 2.0))
    if cap <= 0:
        cap = 1.0

    risk_assets = [
        s.strip().upper()
        for s in str(getattr(settings, "portfolio_risk_assets", "") or "").split(",")
        if s.strip()
    ]
    if not risk_assets:
        risk_assets = ["NASDAQ", "BTC", "COPPER", "REMX", "ALUMINUM", "URANIUM"]

    mult_map = {
        "NORMAL": float(getattr(settings, "portfolio_macro_multiplier_normal", 1.0)),
        "DEFCON2": float(getattr(settings, "portfolio_macro_multiplier_defcon2", 1.0)),
        "DEFCON1": float(getattr(settings, "portfolio_macro_multiplier_defcon1", 1.0)),
        "WARMUP": 0.0,
    }
    macro_mult = float(mult_map.get(str(macro_state or "").upper(), 1.0))

    # Optional: cycle-based risk scaling (global risk appetite multiplier).
    cycle_mult = 1.0
    cycle_diag: Dict[str, Optional[float]] = {}
    if bool(getattr(settings, "portfolio_use_cycles", False)):
        snap = load_cycle_snapshot(
            as_of,
            csv_name=str(getattr(settings, "portfolio_cycles_csv_name", "cycles_daily.csv") or "cycles_daily.csv"),
        )
        if snap is not None:
            cycle_mult = float(snap.risk_multiplier or 1.0)
            cycle_diag = {
                "risk_multiplier": cycle_mult,
                "price_cycle_z": snap.price_cycle_z,
                "vol_z": snap.vol_z,
                "wave_7y": snap.wave_7y,
                "wave_7y_phase": snap.wave_7y_phase,
                "vol_wave_10y": snap.vol_wave_10y,
                "vol_wave_10y_phase": snap.vol_wave_10y_phase,
                "as_of_date": snap.as_of_date,
            }

    # Optional: Fear/Euphoria overlay (only FEAR reduces risk + cap).
    fe_diag: Dict[str, Optional[float]] = {}
    fear_risk_mult = 1.0
    fear_cap = None
    if bool(getattr(settings, "portfolio_use_fear_euphoria", True)):
        try:
            fe = load_fear_euphoria_snapshot(as_of)
        except Exception:
            fe = None
        if fe is not None:
            fe_diag = {
                "months_until_fear": fe.months_until_fear,
                "months_until_euphoria": fe.months_until_euphoria,
                "confidence": fe.confidence,
                "fear_window_36m": fe.fear_window_36m,
                "euphoria_window_36m": fe.euphoria_window_36m,
                "fear_trigger": fe.fear_trigger,
                "euphoria_trigger": fe.euphoria_trigger,
                "as_of_date": fe.as_of_date,
            }
            lvl = int(fe.fear_level or 0) if (fe.fear_level is not None) else (1 if int(fe.fear_trigger or 0) == 1 else 0)
            if lvl > 0:
                # Tiered defense levels (1-3). Default values are in app/settings.py.
                if lvl == 1:
                    fear_risk_mult = float(getattr(settings, "portfolio_fear_level1_risk_multiplier", 0.85))
                    fear_cap = float(getattr(settings, "portfolio_fear_level1_leverage_cap", 1.2))
                elif lvl == 2:
                    fear_risk_mult = float(getattr(settings, "portfolio_fear_level2_risk_multiplier", 0.70))
                    fear_cap = float(getattr(settings, "portfolio_fear_level2_leverage_cap", 1.0))
                else:
                    fear_risk_mult = float(getattr(settings, "portfolio_fear_level3_risk_multiplier", 0.50))
                    fear_cap = float(getattr(settings, "portfolio_fear_level3_leverage_cap", 0.6))

                fe_diag["fear_level"] = lvl
                if fear_cap and fear_cap > 0:
                    cap = min(cap, fear_cap)

    weights_raw: Dict[str, float] = {}
    diagnostics: Dict[str, Dict] = {}
    need = max(ma_win, vol_win + 1)

    for asset_id in px.columns:
        # Strict alignment: do not dropna() here. Use the full series up to as_of.
        s = px_upto[asset_id]
        if len(s) < need:
            diagnostics[asset_id] = {"eligible": False, "reason": f"need>={need} obs", "have": int(len(s))}
            continue

        price = float(s.iloc[-1])
        if not math.isfinite(price):
            diagnostics[asset_id] = {"eligible": False, "reason": "missing data on current date"}
            continue

        ma_series = s.tail(ma_win)
        if ma_series.isna().any():
            ma = float("nan")
        else:
            ma = float(ma_series.mean())

        trend_up = price >= ma if (ma_win > 1 and math.isfinite(ma)) else False

        rets_series = s.pct_change(fill_method=None).tail(vol_win)
        if rets_series.isna().any():
            vol_ann = float("nan")
        else:
            rets_valid = rets_series.dropna()
            vol_ann = float(rets_valid.std()) * math.sqrt(252) if len(rets_valid) >= 2 else float("nan")

        w = 0.0
        if trend_up and vol_ann > 0 and math.isfinite(vol_ann):
            w = target_vol / vol_ann
            if w > cap:
                w = cap
            if asset_id in risk_assets:
                w *= (macro_mult * float(cycle_mult) * float(fear_risk_mult))

        weights_raw[asset_id] = float(max(0.0, w))
        diagnostics[asset_id] = {
            "eligible": True,
            "price": price,
            "ma": ma,
            "trend": "UP" if trend_up else "DOWN",
            "vol_ann": vol_ann if math.isfinite(vol_ann) else None,
            "raw_weight": weights_raw[asset_id],
        }

    if cycle_diag:
        diagnostics["__cycles__"] = cycle_diag

    if fe_diag:
        diagnostics["__fear_euphoria__"] = fe_diag

    gross = float(sum(weights_raw.values()))
    scale = 1.0
    if gross > cap and gross > 0:
        scale = cap / gross

    weights = {k: float(v * scale) for k, v in weights_raw.items() if v and (v * scale) > 0}
    gross_scaled = float(sum(weights.values()))
    cash_weight = float(1.0 - gross_scaled)

    return PortfolioRecommendation(
        as_of_date=str(as_of),
        portfolio_date=str(portfolio_ts.date()),
        model=model,
        target_vol_ann=target_vol,
        vol_window_days=vol_win,
        ma_window_days=ma_win,
        leverage_cap=cap,
        gross_exposure=gross_scaled,
        cash_weight=cash_weight,
        weights=weights,
        diagnostics=diagnostics,
    )


def recommend_portfolio_from_px(
    as_of: date, macro_state: str, px: pd.DataFrame
) -> Optional[PortfolioRecommendation]:
    settings = get_settings()
    return _compute_portfolio_from_px(as_of, macro_state, px, settings)


def recommend_portfolio(as_of: date, macro_state: str, conn=None) -> Optional[PortfolioRecommendation]:
    """
    Recommend a multi-asset allocation from the asset universe CSV/DB.
    """
    settings = get_settings()
    px = load_asset_universe(conn)
    return _compute_portfolio_from_px(as_of, macro_state, px, settings)
