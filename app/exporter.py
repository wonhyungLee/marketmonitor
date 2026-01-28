import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from zoneinfo import ZoneInfo

from app.portfolio import load_asset_universe, recommend_portfolio_from_px
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
    return export_series_csv(conn, "NASDAQ_DLY_IXIC", "1D", out_path=out_path, value_column="close")


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
