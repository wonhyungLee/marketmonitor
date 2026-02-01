import argparse
import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


def _read_csv_any_encoding(path: Path) -> pd.DataFrame:
    last_err: Optional[Exception] = None
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as exc:
            last_err = exc
    if last_err:
        raise last_err
    return pd.read_csv(path)


def _sniff_asset_universe_csv(repo_root: Path) -> Path:
    for p in repo_root.rglob("*.csv"):
        if not p.is_file():
            continue
        try:
            head = p.open("rb").read(4096)
        except Exception:
            continue
        txt = head.decode("utf-8-sig", errors="ignore")
        if txt.startswith("time") and ("BTCUSD" in txt) and ("USDKRW" in txt) and ("XAUUSD" in txt):
            return p
    raise FileNotFoundError("asset universe CSV not found (expected BTCUSD/USDKRW/XAUUSD columns)")


def _asset_col(df: pd.DataFrame, prefix: str) -> Optional[str]:
    cols = [c for c in df.columns if str(c).startswith(prefix)]
    return cols[0] if cols else None


ASSET_SERIES_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "NASDAQ": ("NASDAQ_DLY_IXIC", "US100", "CAPITALCOM_US100", "NASDAQ"),
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


def _load_asset_prices(asset_csv: Path, bond10y_duration: float, bond2y_duration: float, bond_add_carry: bool) -> pd.DataFrame:
    raw = _read_csv_any_encoding(asset_csv)
    if "time" not in raw.columns or "close" not in raw.columns:
        raise ValueError("asset csv must include columns: time, close")

    colmap: Dict[str, str] = {"NASDAQ": "close"}
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

    raw = raw.copy()
    raw["time"] = pd.to_datetime(raw["time"], errors="coerce")
    raw = raw.dropna(subset=["time"]).sort_values("time").set_index("time")

    px = raw[list(colmap.values())].rename(columns={v: k for k, v in colmap.items()})

    # Bonds: synthesize price indices from yields if present.
    y10_col = _asset_col(raw, "US10Y")
    y2_col = _asset_col(raw, "US02Y") or _asset_col(raw, "US2Y")
    if y10_col:
        px["UST10Y"] = _yield_to_bond_index(raw[y10_col], duration=bond10y_duration, add_carry=bond_add_carry)
    if y2_col:
        px["UST2Y"] = _yield_to_bond_index(raw[y2_col], duration=bond2y_duration, add_carry=bond_add_carry)
    return px


def _load_asset_prices_from_db(
    db_path: Path, bond10y_duration: float, bond2y_duration: float, bond_add_carry: bool
) -> pd.DataFrame:
    if not db_path.exists():
        raise FileNotFoundError(f"db not found: {db_path}")

    series_ids = set()
    for candidates in ASSET_SERIES_CANDIDATES.values():
        series_ids.update(candidates)
    for candidates in BOND_SERIES_CANDIDATES.values():
        series_ids.update(candidates)

    conn = sqlite3.connect(db_path)
    try:
        placeholders = ", ".join(["?"] * len(series_ids))
        query = f"""
            SELECT series_id, time_utc_ms, interval, value
            FROM market_observations
            WHERE series_id IN ({placeholders}) AND interval = '1D';
        """
        data_frame = pd.read_sql_query(query, conn, params=sorted(series_ids))
    finally:
        conn.close()

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

    y10_col = _pick_series_column(columns, BOND_SERIES_CANDIDATES["UST10Y"])
    if y10_col:
        px["UST10Y"] = _yield_to_bond_index(pivot[y10_col], duration=bond10y_duration, add_carry=bond_add_carry)
    y2_col = _pick_series_column(columns, BOND_SERIES_CANDIDATES["UST2Y"])
    if y2_col:
        px["UST2Y"] = _yield_to_bond_index(pivot[y2_col], duration=bond2y_duration, add_carry=bond_add_carry)
    return px


def _load_states(states_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(states_csv, usecols=["as_of_date", "state"])
    df["as_of_date"] = pd.to_datetime(df["as_of_date"], errors="coerce")
    df = df.dropna(subset=["as_of_date"])
    df = df.sort_values("as_of_date").set_index("as_of_date")
    return df


def _calc_metrics(curve: pd.Series) -> Dict[str, float]:
    curve = curve.dropna()
    if len(curve) < 2:
        return {"cagr": float("nan"), "mdd": float("nan")}
    cagr = float(curve.iloc[-1] ** (252.0 / len(curve)) - 1.0)
    mdd = float((curve / curve.cummax() - 1.0).min())
    return {"cagr": cagr, "mdd": mdd}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset-csv", default="", help="Path to asset universe CSV (default: auto-detect)")
    ap.add_argument("--db-path", default="warroom.db", help="Path to warroom.db")
    ap.add_argument("--use-db", action="store_true", help="Prefer DB asset prices when available")
    ap.add_argument("--states-csv", default="data/market_states_daily.csv")
    ap.add_argument("--out-csv", default="data/portfolio_daily.csv")

    ap.add_argument("--start", default="1988-01-01", help="Report metrics from this date (YYYY-MM-DD)")

    ap.add_argument("--ma", type=int, default=200)
    ap.add_argument("--vol", type=int, default=20)
    ap.add_argument("--target-vol", type=float, default=0.35)
    ap.add_argument("--cap", type=float, default=2.0)
    ap.add_argument("--bond10y-duration", type=float, default=8.5)
    ap.add_argument("--bond2y-duration", type=float, default=1.9)
    ap.add_argument("--bond-no-carry", action="store_true", help="Disable carry term for synthetic bond indices")

    ap.add_argument(
        "--risk-assets",
        default="NASDAQ,BTC,COPPER,REMX,ALUMINUM,URANIUM",
        help="Comma-separated list of assets to down-weight in DEFCON regimes.",
    )
    ap.add_argument("--m-normal", type=float, default=1.0)
    ap.add_argument("--m-defcon2", type=float, default=1.0)
    ap.add_argument("--m-defcon1", type=float, default=1.0)
    args = ap.parse_args()

    repo_root = Path(".")
    db_path = Path(args.db_path)
    use_db = bool(args.use_db or (not args.asset_csv and db_path.exists()))
    if use_db:
        px = _load_asset_prices_from_db(
            db_path,
            bond10y_duration=float(args.bond10y_duration),
            bond2y_duration=float(args.bond2y_duration),
            bond_add_carry=not bool(args.bond_no_carry),
        )
        asset_source = f"db:{db_path.as_posix()}"
    else:
        asset_csv = Path(args.asset_csv) if args.asset_csv else _sniff_asset_universe_csv(repo_root)
        px = _load_asset_prices(
            asset_csv,
            bond10y_duration=float(args.bond10y_duration),
            bond2y_duration=float(args.bond2y_duration),
            bond_add_carry=not bool(args.bond_no_carry),
        )
        asset_source = f"csv:{asset_csv.as_posix()}"

    states = _load_states(Path(args.states_csv))
    df = px.join(states, how="left")
    df["state"] = df["state"].ffill().fillna("WARMUP")

    ma_win = int(args.ma)
    vol_win = int(args.vol)
    target = float(args.target_vol)
    cap = float(args.cap)
    if cap <= 0:
        cap = 1.0

    # Signals
    rets = px.pct_change(fill_method=None)
    ma = px.rolling(ma_win).mean()
    trend_up = px >= ma
    vol_ann = rets.rolling(vol_win).std() * math.sqrt(252)

    w_raw = (target / vol_ann).clip(upper=cap)
    w_raw = w_raw.where(vol_ann > 0)
    w_raw = w_raw.where(trend_up, 0.0)
    w_raw = w_raw.fillna(0.0)

    # Macro multipliers (optional): down-weight "risk assets" under DEFCON.
    risk_assets = [s.strip().upper() for s in str(args.risk_assets or "").split(",") if s.strip()]
    mult_by_state = {"NORMAL": args.m_normal, "DEFCON2": args.m_defcon2, "DEFCON1": args.m_defcon1, "WARMUP": 0.0}
    macro_mult = df["state"].map(lambda s: float(mult_by_state.get(str(s).upper(), 1.0)))
    for a in risk_assets:
        if a in w_raw.columns:
            w_raw[a] = w_raw[a] * macro_mult

    # Scale to leverage cap by total gross exposure.
    sumw = w_raw.sum(axis=1)
    scale = (cap / sumw).where(sumw > cap, 1.0)
    w = w_raw.mul(scale, axis=0)
    gross = w.sum(axis=1)
    cash = 1.0 - gross

    # Portfolio backtest (long-only, 1-day lag; no costs)
    w_lag = w.shift(1).fillna(0.0)
    port_ret = (w_lag * rets.fillna(0.0)).sum(axis=1)
    curve = (1.0 + port_ret).cumprod()

    out = pd.DataFrame(index=px.index)
    out.index.name = "date"
    out["state"] = df["state"]
    out["gross_exposure"] = gross
    out["cash_weight"] = cash
    for a in w.columns:
        out[f"w_{a}"] = w[a]
    out["portfolio_ret"] = port_ret
    out["portfolio_curve"] = curve
    out["portfolio_dd"] = curve / curve.cummax() - 1.0

    out_path = Path(args.out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, encoding="utf-8", float_format="%.6f")

    # Report metrics from --start
    start = pd.to_datetime(args.start)
    curve2 = curve[curve.index >= start]
    m = _calc_metrics(curve2)
    print(f"asset_source: {asset_source}")
    print(f"wrote: {out_path.as_posix()} ({len(out):,} rows)")
    print(f"metrics from {start.date()}: CAGR={m['cagr']*100:.2f}%  MDD={m['mdd']*100:.2f}%  avg_gross={gross.mean():.2f}x")


if __name__ == "__main__":
    main()
