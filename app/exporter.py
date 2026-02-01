import csv
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import pandas as pd

from zoneinfo import ZoneInfo

from app.portfolio import load_asset_universe, recommend_portfolio_from_px
from app.forecast_v1 import build_forecast_v1_daily, DEFAULT_SERIES
from app.timing_v1 import export_timing_v1_daily
from app.settings import get_settings

CSV_HEADER = [
    "as_of_date",
    "state",
    "score",
    "action",
    "equity_weight",
    "trend_signal",
    "trend_price",
    "trend_ma",
    "hard_defcon1",
    "prev_state",
    "streak_ge_2",
    "streak_ge_3_5",
    "streak_lt_2",
    "streak_le_3",
    "T10Y2Y_cross_up",
    "BAML_spread_risk",
    "WEI_recession_trend",
    "COPPER_GOLD_under_ma200",
    "UMCSENT_low",
    "triggers",
]


def backup_daily_states_csv(backup_dir: Optional[Path] = None) -> Optional[Path]:
    base_dir = Path(__file__).resolve().parent.parent
    src = base_dir / "data" / "market_states_daily.csv"
    if not src.exists():
        return None
    backup_dir = backup_dir or (base_dir / "data" / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"market_states_daily_{ts}.csv"
    shutil.copy2(src, dst)
    return dst


def _parse_json(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stringify_triggers(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " | ".join([str(v) for v in value if v])
    return str(value)


def _pick_first(dct: Dict[str, Any], keys: list[str]) -> Optional[Any]:
    for key in keys:
        if key in dct:
            return dct.get(key)
    return None


def _clean(value: Any) -> Any:
    return "" if value is None else value


def export_daily_states_csv(conn, out_path: Optional[Path] = None) -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    out_path = out_path or (base_dir / "data" / "market_states_daily.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing: Dict[str, Dict[str, Any]] = {}
    if out_path.exists():
        with out_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date = (row.get("as_of_date") or "").strip()
                if not date:
                    continue
                existing[date] = {k: row.get(k, "") for k in CSV_HEADER}

    rows = conn.execute(
        """
        SELECT as_of_date, state, score, reasons_json
        FROM daily_states
        ORDER BY as_of_date ASC
        """
    ).fetchall()

    for row in rows:
        reasons = _parse_json(row["reasons_json"])
        triggers = _stringify_triggers(reasons.get("triggers"))

        allocation = reasons.get("allocation")
        if not isinstance(allocation, dict):
            allocation = {}

        trend = reasons.get("trend")
        if not isinstance(trend, dict):
            trend = {}

        streaks = reasons.get("streaks")
        if not isinstance(streaks, dict):
            streaks = {}

        components = reasons.get("components")
        if not isinstance(components, dict):
            components = {}

        equity_weight = _to_float(allocation.get("equity_weight"))
        if equity_weight is None:
            equity_pct = _to_float(allocation.get("equity_weight_pct"))
            if equity_pct is not None:
                equity_weight = equity_pct / 100.0

        hard_defcon1 = reasons.get("hard_defcon1")
        if hard_defcon1 is None:
            hard_defcon1 = "hard defcon1" in triggers.lower()
        if hard_defcon1 in ("", None):
            hard_defcon1_val = ""
        else:
            hard_defcon1_val = 1 if bool(hard_defcon1) else 0

        data = {
            "as_of_date": row["as_of_date"],
            "state": row["state"],
            "score": row["score"],
            "action": allocation.get("action") or reasons.get("action") or "",
            "equity_weight": equity_weight,
            "trend_signal": trend.get("signal") or reasons.get("trend_signal") or "",
            "trend_price": trend.get("price"),
            "trend_ma": trend.get("ma"),
            "hard_defcon1": hard_defcon1_val,
            "prev_state": reasons.get("prev_state") or "",
            "streak_ge_2": _pick_first(streaks, ["streak_ge_2", "streak_ge_3"]),
            "streak_ge_3_5": _pick_first(streaks, ["streak_ge_3_5", "streak_ge_5"]),
            "streak_lt_2": _pick_first(streaks, ["streak_lt_2", "streak_le_2"]),
            "streak_le_3": _pick_first(streaks, ["streak_le_3"]),
            "T10Y2Y_cross_up": components.get("T10Y2Y_cross_up"),
            "BAML_spread_risk": components.get("BAML_spread_risk"),
            "WEI_recession_trend": components.get("WEI_recession_trend"),
            "COPPER_GOLD_under_ma200": components.get("COPPER_GOLD_under_ma200"),
            "UMCSENT_low": components.get("UMCSENT_low"),
            "triggers": triggers,
        }

        date_key = data["as_of_date"]
        if date_key:
            existing[date_key] = {k: _clean(v) for k, v in data.items()}

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for date_key in sorted(existing.keys()):
            row = existing[date_key]
            writer.writerow({k: row.get(k, "") for k in CSV_HEADER})

    return out_path


def export_series_csv(
    conn,
    series_id: str,
    interval: str,
    out_path: Optional[Path] = None,
    value_column: str = "close",
) -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    out_path = out_path or (base_dir / "data" / f"{series_id.lower()}_{interval.lower()}.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = conn.execute(
        """
        SELECT time_utc_ms, value
        FROM market_observations
        WHERE series_id = ? AND interval = ?
        ORDER BY time_utc_ms ASC
        """,
        (series_id, interval),
    ).fetchall()

    # Keep the last observation per date (UTC) to avoid duplicates.
    by_date: Dict[str, float] = {}
    for time_utc_ms, value in rows:
        if time_utc_ms is None:
            continue
        date_str = datetime.fromtimestamp(time_utc_ms / 1000, tz=timezone.utc).date().isoformat()
        by_date[date_str] = value

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", value_column])
        for date_str in sorted(by_date.keys()):
            writer.writerow([date_str, by_date[date_str]])

    return out_path


def export_nasdaq_1d_csv(conn, out_path: Optional[Path] = None) -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    out_path = out_path or (base_dir / "data" / "nasdaq_dly_ixic_1d.csv")

    rows = conn.execute(
        """
        SELECT time_utc_ms, value
        FROM market_observations
        WHERE series_id = ? AND interval = ?
        ORDER BY time_utc_ms ASC
        """,
        ("NASDAQ_DLY_IXIC", "1D"),
    ).fetchall()

    by_date: Dict[str, float] = {}
    for time_utc_ms, value in rows:
        if time_utc_ms is None:
            continue
        date_str = datetime.fromtimestamp(time_utc_ms / 1000, tz=timezone.utc).date().isoformat()
        by_date[date_str] = value

    # Emit rows for daily_states dates even if NASDAQ is missing (blank close).
    dates = None
    try:
        state_rows = conn.execute(
            "SELECT as_of_date FROM daily_states ORDER BY as_of_date ASC"
        ).fetchall()
        dates = [d for (d,) in state_rows if d]
    except Exception:
        dates = None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "close"])
        if dates:
            for date_str in dates:
                writer.writerow([date_str, by_date.get(date_str, "")])
        else:
            for date_str in sorted(by_date.keys()):
                writer.writerow([date_str, by_date[date_str]])

    return out_path


def export_forecast_v1_csv(conn, out_path: Optional[Path] = None) -> Path:
    """Export Forecast v1 probabilities (Crisis + Euphoria).

    Emits:
      - forecast_v1_daily.csv

    The file is regenerated from the DB each run (no incremental merge).
    """
    base_dir = Path(__file__).resolve().parent.parent
    out_path = out_path or (base_dir / "data" / "forecast_v1_daily.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Fetch raw series data directly (avoid load_asset_universe filtering)
    # Ensure we get all DEFAULT_SERIES plus components for ratio calculation
    target_series = set(DEFAULT_SERIES)
    target_series.add("COPPER")
    target_series.add("XAUUSD")
    # Also fetch FXCM_COPPER as fallback for COPPER
    target_series.add("FXCM_COPPER")

    placeholders = ",".join("?" for _ in target_series)
    rows = conn.execute(
        f"""
        SELECT series_id, time_utc_ms, value
        FROM market_observations
        WHERE series_id IN ({placeholders})
        ORDER BY time_utc_ms ASC
        """,
        list(target_series),
    ).fetchall()

    if not rows:
        # Create empty file with header
        pd.DataFrame(columns=["date", "model"]).to_csv(out_path, index=False, encoding="utf-8")
        return out_path

    # 2. Pivot to wide format: index=date, columns=series_id
    df = pd.DataFrame(rows, columns=["series_id", "time_utc_ms", "value"])
    df["date"] = pd.to_datetime(df["time_utc_ms"], unit="ms", utc=True).dt.date
    # Keep last value per day
    wide = df.pivot_table(index="date", columns="series_id", values="value", aggfunc="last")
    wide = wide.sort_index()

    # 3. Data cleanup / Feature engineering
    # Merge FXCM_COPPER into COPPER if needed
    if "FXCM_COPPER" in wide.columns:
        if "COPPER" in wide.columns:
            wide["COPPER"] = wide["COPPER"].combine_first(wide["FXCM_COPPER"])
        else:
            wide["COPPER"] = wide["FXCM_COPPER"]
    
    # Calculate COPPER_GOLD_RATIO if missing or partial
    # COPPER_GOLD_RATIO = ln(COPPER / XAUUSD) usually, or just ratio. 
    # Check existing data range. 
    # The existing DB likely has it pre-calculated, but filling gaps is good.
    if "COPPER" in wide.columns and "XAUUSD" in wide.columns:
        try:
            # Assume ratio is simple division. If original series was log, this might be different.
            # But typically it is Price(Copper) / Price(Gold).
            # We'll compute it and fill gaps in existing column.
            computed = wide["COPPER"] / wide["XAUUSD"]
            if "COPPER_GOLD_RATIO" in wide.columns:
                wide["COPPER_GOLD_RATIO"] = wide["COPPER_GOLD_RATIO"].combine_first(computed)
            else:
                wide["COPPER_GOLD_RATIO"] = computed
        except Exception:
            pass

    # 4. Prepare inputs for forecast engine
    asset_universe = wide.reset_index() # 'date' becomes a column
    # Ensure date is datetime64 for merge
    asset_universe["date"] = pd.to_datetime(asset_universe["date"])

    try:
        st = pd.read_sql_query(
            "SELECT as_of_date AS date, state FROM daily_states ORDER BY as_of_date ASC",
            conn,
        )
    except Exception:
        st = pd.DataFrame(columns=["date", "state"])

    # 5. Build Forecast
    forecast_df, _ = build_forecast_v1_daily(asset_universe, st)
    forecast_df.to_csv(out_path, index=False, encoding="utf-8")
    return out_path




def export_timing_v1_csv(conn, out_path: Path | None = None) -> Path:
    """Export Timing v1 (when will events begin?)

    Delegates to app.timing_v1.export_timing_v1_daily which reads from CSV.
    The 'conn' argument is ignored but kept for compatibility with run_daily.py.
    """
    base_dir = Path(__file__).resolve().parent.parent
    data_dir = base_dir / "data"
    out_path = out_path or (data_dir / "timing_v1_daily.csv")

    # Delegate to the new file-based exporter
    res = export_timing_v1_daily(data_dir=data_dir, out_name=out_path.name)

    if res is None:
        # Fallback: create empty file if generation failed
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.touch()

    return out_path

def export_cycles_csv(conn, out_dir: Optional[Path] = None) -> tuple[Optional[Path], Optional[Path]]:
    """Export NASDAQ cycle indicators.

    Emits two files into ./data (or out_dir):
      - cycles_monthly.csv: long-run cycle features on monthly data
      - cycles_daily.csv: daily forward-filled snapshot used by portfolio scaling

    Returns: (monthly_path, daily_path)
    """
    base_dir = Path(__file__).resolve().parent.parent
    out_dir = out_dir or (base_dir / "data")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_m = out_dir / "cycles_monthly.csv"
    out_d = out_dir / "cycles_daily.csv"

    # Pull NASDAQ daily closes.
    rows = conn.execute(
        """
        SELECT time_utc_ms, value
        FROM market_observations
        WHERE series_id = ? AND interval = ?
        ORDER BY time_utc_ms ASC
        """,
        ("NASDAQ_DLY_IXIC", "1D"),
    ).fetchall()
    if not rows:
        return None, None

    # Keep last close per UTC date.
    by_date: Dict[str, float] = {}
    for time_utc_ms, value in rows:
        if time_utc_ms is None:
            continue
        date_str = datetime.fromtimestamp(time_utc_ms / 1000, tz=timezone.utc).date().isoformat()
        by_date[date_str] = value

    daily = pd.Series(by_date).sort_index()
    daily.index = pd.to_datetime(daily.index)
    daily = pd.to_numeric(daily, errors="coerce").dropna()
    if daily.empty:
        return None, None

    # Legacy implementation (kept for rollback). Import lazily.
    from app.cycles import build_cycles_from_nasdaq_daily

    cycles_m = build_cycles_from_nasdaq_daily(daily)
    if cycles_m.empty:
        return None, None

    # Write monthly
    cycles_m_out = cycles_m.copy()
    cycles_m_out.index.name = "time"
    cycles_m_out.reset_index().to_csv(out_m, index=False, encoding="utf-8")

    # Forward-fill monthly snapshot to daily dates.
    daily_dates = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    snap = cycles_m[[
        "risk_multiplier",
        "price_cycle_z",
        "vol_z",
        "wave_7y",
        "wave_7y_phase",
        "vol_wave_10y",
        "vol_wave_10y_phase",
    ]].copy()
    snap.index = snap.index.to_period("M").to_timestamp("M")
    snap_d = snap.reindex(pd.to_datetime(daily_dates).to_period("M").to_timestamp("M"))
    snap_d.index = pd.to_datetime(daily_dates)
    snap_d = snap_d.ffill()
    snap_d = snap_d.reset_index().rename(columns={"index": "date"})
    snap_d.to_csv(out_d, index=False, encoding="utf-8")

    return out_m, out_d


def export_fear_euphoria_csv(conn, out_dir: Optional[Path] = None) -> tuple[Optional[Path], Optional[Path]]:
    """Export fear/euphoria forecast windows + confirm triggers.

    Emits two files into ./data (or out_dir):
      - fear_euphoria_monthly.csv: phase->months-ahead forecast + confidence
      - fear_euphoria_daily.csv: daily forward-filled + confirm trigger flags

    Returns: (monthly_path, daily_path)
    """
    base_dir = Path(__file__).resolve().parent.parent
    out_dir = out_dir or (base_dir / "data")
    out_dir.mkdir(parents=True, exist_ok=True)
    cycles_m_path = out_dir / "cycles_monthly.csv"
    cycles_d_path = out_dir / "cycles_daily.csv"
    out_m = out_dir / "fear_euphoria_monthly.csv"
    out_d = out_dir / "fear_euphoria_daily.csv"

    # Need cycles_monthly (for phase/amp). If missing, try to create it.
    if not cycles_m_path.exists() or not cycles_d_path.exists():
        export_cycles_csv(conn, out_dir=out_dir)
    if not cycles_m_path.exists():
        return None, None

    try:
        cycles_m = pd.read_csv(cycles_m_path)
    except Exception:
        return None, None
    if cycles_m.empty:
        return None, None

    # Legacy implementation (kept for rollback). Import lazily.
    from app.fear_euphoria import build_fear_euphoria_from_cycles_monthly, compute_daily_triggers

    fe_m = build_fear_euphoria_from_cycles_monthly(cycles_m)
    if fe_m.empty:
        return None, None
    fe_m.to_csv(out_m, index=False, encoding="utf-8")

    # Forward-fill monthly features to daily range.
    # Use NASDAQ daily close range (DB) as the daily index, then merge triggers.
    rows = conn.execute(
        """
        SELECT time_utc_ms, value
        FROM market_observations
        WHERE series_id = ? AND interval = ?
        ORDER BY time_utc_ms ASC
        """,
        ("NASDAQ_DLY_IXIC", "1D"),
    ).fetchall()
    if not rows:
        return out_m, None

    by_date: Dict[str, float] = {}
    for time_utc_ms, value in rows:
        if time_utc_ms is None:
            continue
        date_str = datetime.fromtimestamp(time_utc_ms / 1000, tz=timezone.utc).date().isoformat()
        by_date[date_str] = value

    daily_close = pd.Series(by_date).sort_index()
    daily_close.index = pd.to_datetime(daily_close.index)
    daily_close = pd.to_numeric(daily_close, errors="coerce").dropna()
    if daily_close.empty:
        return out_m, None

    # Use observed dates (trading days). This avoids weekend forward-fill artifacts in returns/vol.
    daily_dates = pd.DatetimeIndex(daily_close.index).sort_values()

    fe_m2 = fe_m.copy()
    fe_m2["time"] = pd.to_datetime(fe_m2["time"], errors="coerce")
    fe_m2 = fe_m2.dropna(subset=["time"]).sort_values("time")
    fe_m2 = fe_m2.set_index(fe_m2["time"].dt.to_period("M").dt.to_timestamp("M")).drop(columns=["time"])

    fe_d = fe_m2.reindex(pd.to_datetime(daily_dates).to_period("M").to_timestamp("M"))
    fe_d.index = pd.to_datetime(daily_dates)
    fe_d = fe_d.ffill()
    fe_d.index.name = "time"

    # Daily macro state (DEFCON1/2) for confirm trigger.
    state_rows = conn.execute(
        """
        SELECT as_of_date, state
        FROM daily_states
        ORDER BY as_of_date ASC
        """
    ).fetchall()
    st = pd.Series({r[0]: r[1] for r in state_rows})
    st.index = pd.to_datetime(st.index, errors="coerce")

    trig = compute_daily_triggers(
        daily_close=daily_close,
        daily_state=st,
        forecast_daily=fe_d,
    )

    merged = pd.concat([fe_d, trig], axis=1)
    merged = merged.reset_index().rename(columns={"index": "date"})
    merged.to_csv(out_d, index=False, encoding="utf-8")

    return out_m, out_d



def export_fear_euphoria_calendar(conn, out_dir: Optional[Path] = None, *, horizon_months: int = 60, lookback_months: int = 12) -> tuple[Optional[Path], Optional[Path]]:
    """Export a month-level 'calendar' view for fear/euphoria windows.

    Generates:
      - fear_euphoria_calendar.csv: month_end rows with window flags (24/36m)
      - fear_euphoria_forecast.ics: iCal events for window starts + predicted peak/trough

    The calendar is anchored to the *latest* monthly forecast row.

    Returns: (csv_path, ics_path)
    """
    base_dir = Path(__file__).resolve().parent.parent
    out_dir = out_dir or (base_dir / "data")
    out_dir.mkdir(parents=True, exist_ok=True)
    fe_m_path = out_dir / "fear_euphoria_monthly.csv"
    cal_csv = out_dir / "fear_euphoria_calendar.csv"
    cal_ics = out_dir / "fear_euphoria_forecast.ics"

    if not fe_m_path.exists():
        # attempt to create
        export_fear_euphoria_csv(conn, out_dir=out_dir)
    if not fe_m_path.exists():
        return None, None

    try:
        fe_m = pd.read_csv(fe_m_path)
    except Exception:
        return None, None
    if fe_m.empty:
        return None, None

    fe_m["time"] = pd.to_datetime(fe_m.get("time"), errors="coerce")
    fe_m = fe_m.dropna(subset=["time"]).sort_values("time")
    if fe_m.empty:
        return None, None

    last = fe_m.iloc[-1]
    as_of = pd.Timestamp(last["time"]).to_period("M").to_timestamp("M")

    def _to_float(v):
        try:
            return float(v)
        except Exception:
            return None

    mu_f = _to_float(last.get("months_until_fear"))
    mu_e = _to_float(last.get("months_until_euphoria"))

    # Predicted peak/trough month (month-end) from latest estimate.
    peak_f = as_of + pd.DateOffset(months=int(round(mu_f))) if mu_f is not None and mu_f >= 0 else None
    trough_e = as_of + pd.DateOffset(months=int(round(mu_e))) if mu_e is not None and mu_e >= 0 else None

    fear36_start = peak_f - pd.DateOffset(months=36) if peak_f is not None else None
    fear24_start = peak_f - pd.DateOffset(months=24) if peak_f is not None else None
    euph36_start = trough_e - pd.DateOffset(months=36) if trough_e is not None else None
    euph24_start = trough_e - pd.DateOffset(months=24) if trough_e is not None else None

    start = as_of - pd.DateOffset(months=int(lookback_months))
    end = as_of + pd.DateOffset(months=int(horizon_months))
    months = pd.period_range(start=start.to_period("M"), end=end.to_period("M"), freq="M").to_timestamp("M")

    def _in_range(ts, a, b):
        if ts is None or a is None or b is None:
            return False
        return (ts >= a) and (ts <= b)

    rows = []
    for ts in months:
        f36 = int(_in_range(ts, fear36_start, peak_f)) if peak_f is not None else 0
        f24 = int(_in_range(ts, fear24_start, peak_f)) if peak_f is not None else 0
        e36 = int(_in_range(ts, euph36_start, trough_e)) if trough_e is not None else 0
        e24 = int(_in_range(ts, euph24_start, trough_e)) if trough_e is not None else 0

        label = []
        if f36:
            label.append("FEAR")
        if e36:
            label.append("EUPH")
        label = "+".join(label) if label else "NONE"

        rows.append(
            {
                "month_end": ts.date().isoformat(),
                "as_of_month_end": as_of.date().isoformat(),
                "fear_peak_month_end": peak_f.date().isoformat() if peak_f is not None else "",
                "euphoria_trough_month_end": trough_e.date().isoformat() if trough_e is not None else "",
                "fear_window_36m": f36,
                "fear_window_24m": f24,
                "euphoria_window_36m": e36,
                "euphoria_window_24m": e24,
                "label": label,
            }
        )

    pd.DataFrame(rows).to_csv(cal_csv, index=False, encoding="utf-8")

    # iCal (ICS): simple all-day events at month-start with 1-day duration
    def _ics_date(ts: pd.Timestamp) -> str:
        return ts.strftime("%Y%m%d")

    def _vevent(uid: str, summary: str, dt: pd.Timestamp, desc: str = "") -> str:
        # DTSTART/DTEND as all-day (date)
        d0 = _ics_date(dt.to_period("M").to_timestamp("D"))
        d1 = _ics_date((dt.to_period("M").to_timestamp("D") + pd.Timedelta(days=1)))
        out = [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART;VALUE=DATE:{d0}",
            f"DTEND;VALUE=DATE:{d1}",
            f"SUMMARY:{summary}",
        ]
        if desc:
            out.append(f"DESCRIPTION:{desc}")
        out.append("END:VEVENT")
        return "\n".join(out)

    vevents = []
    if fear36_start is not None:
        vevents.append(_vevent("fear-window36-start@marketmonitor", "FEAR window start (36m)", fear36_start, "Cycle-based forecast window begins"))
    if fear24_start is not None:
        vevents.append(_vevent("fear-window24-start@marketmonitor", "FEAR window start (24m)", fear24_start, "Closer approach to FEAR peak"))
    if peak_f is not None:
        vevents.append(_vevent("fear-peak@marketmonitor", "FEAR peak (cycle forecast)", peak_f, "Predicted volatility cycle peak"))

    if euph36_start is not None:
        vevents.append(_vevent("euph-window36-start@marketmonitor", "EUPH window start (36m)", euph36_start, "Cycle-based forecast window begins"))
    if euph24_start is not None:
        vevents.append(_vevent("euph-window24-start@marketmonitor", "EUPH window start (24m)", euph24_start, "Closer approach to EUPH trough"))
    if trough_e is not None:
        vevents.append(_vevent("euph-trough@marketmonitor", "EUPH trough (cycle forecast)", trough_e, "Predicted volatility cycle trough"))

    ics = "\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MarketMonitor//FearEuphoria//EN",
        *vevents,
        "END:VCALENDAR",
        "",
    ])
    cal_ics.write_text(ics, encoding="utf-8")

    return cal_csv, cal_ics


def export_asset_universe_csv(conn, out_path: Optional[Path] = None) -> Path:
    """
    Export all observations from the database into a single pivoted CSV file.
    Columns: date, series_id_1, series_id_2, ...
    """
    import pandas as pd
    base_dir = Path(__file__).resolve().parent.parent
    out_path = out_path or (base_dir / "data" / "asset_universe.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = conn.execute(
        """
        SELECT series_id, time_utc_ms, value
        FROM market_observations
        ORDER BY time_utc_ms ASC
        """
    ).fetchall()

    if not rows:
        return out_path

    df = pd.DataFrame(rows, columns=["series_id", "time_utc_ms", "value"])
    df["date"] = pd.to_datetime(df["time_utc_ms"], unit="ms", utc=True).dt.date
    
    # Pivot: dates as index, series_ids as columns
    # Handle duplicates by taking the last value for each date/series
    pivot = df.pivot_table(index="date", columns="series_id", values="value", aggfunc="last")
    pivot = pivot.sort_index()

    # Normalize FXCM_COPPER -> COPPER (avoid duplicate columns)
    if "FXCM_COPPER" in pivot.columns:
        if "COPPER" in pivot.columns:
            pivot["COPPER"] = pivot["COPPER"].combine_first(pivot["FXCM_COPPER"])
        else:
            pivot = pivot.rename(columns={"FXCM_COPPER": "COPPER"})
        pivot = pivot.drop(columns=["FXCM_COPPER"], errors="ignore")

    # Merge with existing CSV if present (DB values take precedence)
    try:
        if out_path.exists():
            existing = pd.read_csv(out_path)
            if "date" in existing.columns:
                existing["date"] = pd.to_datetime(existing["date"], errors="coerce")
                existing = existing.dropna(subset=["date"]).set_index("date").sort_index()
                if "FXCM_COPPER" in existing.columns:
                    if "COPPER" in existing.columns:
                        existing["COPPER"] = existing["COPPER"].combine_first(existing["FXCM_COPPER"])
                    else:
                        existing = existing.rename(columns={"FXCM_COPPER": "COPPER"})
                    existing = existing.drop(columns=["FXCM_COPPER"], errors="ignore")
                pivot = pivot.combine_first(existing)
    except Exception:
        pass

    pivot.to_csv(out_path, encoding="utf-8")
    return out_path


def export_webhook_records_csv(conn, out_path: Optional[Path] = None) -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    out_path = out_path or (base_dir / "data" / "webhook_records.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = conn.execute(
        """
        SELECT series_id, time_utc_ms, interval, value, received_at
        FROM market_observations
        ORDER BY received_at ASC
        """
    ).fetchall()

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["series_id", "time_utc_ms", "interval", "value", "received_at"])
        writer.writerows(rows)

    return out_path


def _load_existing_portfolio_csv(out_path: Path) -> tuple[Dict[str, Dict[str, Any]], list[str]]:
    if not out_path.exists():
        return {}, []
    with out_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        existing: Dict[str, Dict[str, Any]] = {}
        for row in reader:
            date = (row.get("date") or "").strip()
            if date:
                existing[date] = row
    return existing, fieldnames


def export_portfolio_recent_csv(
    conn,
    out_path: Optional[Path] = None,
    days: int = 30,
    end_date: Optional[date] = None,
) -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    out_path = out_path or (base_dir / "data" / "portfolio_daily.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing, fieldnames = _load_existing_portfolio_csv(out_path)

    settings = get_settings()
    tz = ZoneInfo(settings.as_of_tz)

    if end_date is None:
        row = conn.execute("SELECT MAX(time_utc_ms) FROM market_observations").fetchone()
        if not row or row[0] is None:
            return out_path
        dt = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).astimezone(tz)
        end_date = dt.date()

    start_date = end_date - timedelta(days=max(1, int(days)) - 1)

    states = conn.execute(
        """
        SELECT as_of_date, state
        FROM daily_states
        WHERE as_of_date BETWEEN ? AND ?
        ORDER BY as_of_date ASC
        """,
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    state_map = {row[0]: row[1] for row in states}

    px = load_asset_universe(conn)
    if px.empty:
        return out_path

    weight_cols: set[str] = set()
    cur = start_date
    while cur <= end_date:
        date_str = cur.isoformat()
        state = state_map.get(date_str)
        if state:
            rec = recommend_portfolio_from_px(cur, state, px)
            if rec is not None:
                row = dict(existing.get(date_str, {}))
                for k in list(row.keys()):
                    if k.startswith("w_"):
                        row.pop(k, None)
                row["date"] = date_str
                row["state"] = state
                row["gross_exposure"] = f"{rec.gross_exposure:.6f}"
                row["cash_weight"] = f"{rec.cash_weight:.6f}"
                for asset, weight in rec.weights.items():
                    col = f"w_{asset}"
                    row[col] = f"{weight:.6f}"
                    weight_cols.add(col)
                existing[date_str] = row
        cur += timedelta(days=1)

    if not fieldnames:
        fieldnames = ["date", "state", "gross_exposure", "cash_weight"]
    if "date" not in fieldnames:
        fieldnames.insert(0, "date")
    for col in ("state", "gross_exposure", "cash_weight"):
        if col not in fieldnames:
            fieldnames.append(col)
    for col in sorted(weight_cols):
        if col not in fieldnames:
            fieldnames.append(col)

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for date_key in sorted(existing.keys()):
            row = existing[date_key]
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    return out_path
