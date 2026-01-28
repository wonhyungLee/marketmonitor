import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from app import db
try:
    from app.settings import get_settings
except Exception:
    get_settings = None


FILENAME_SERIES_MAP: Dict[str, str] = {
    "FRED_T10Y2Y": "T10Y2Y",
    "FRED_BAMLH0A0HYM2": "BAMLH0A0HYM2",
    "COMEX_DL_HG1!_COMEX_DL_GC1!": "COPPER_GOLD_RATIO",
    "FRED_WEI": "WEI",
    "FRED_SAHMREALTIME": "SAHMREALTIME",
    "FRED_UMCSENT": "UMCSENT",
    "CAPITALCOM_US100": "US100",
    "NASDAQ_DLY_IXIC": "COMBINED",
}

COMBINED_COLUMN_MAP: Dict[str, str] = {
    "close": "NASDAQ_DLY_IXIC",
    "T10Y2Y": "T10Y2Y",
    "HY_Spread": "BAMLH0A0HYM2",
    "Copper_Gold_Log": "COPPER_GOLD_RATIO",
    "WEI": "WEI",
    "Sahm_Rule": "SAHMREALTIME",
    "UM_Sentiment": "UMCSENT",
}

COLUMN_INTERVAL_OVERRIDE: Dict[str, str] = {
    "T10Y2Y": "1D",
    "BAMLH0A0HYM2": "1D",
    "COPPER_GOLD_RATIO": "1D",
    "WEI": "1W",
    "SAHMREALTIME": "1M",
    "UMCSENT": "1M",
}

ASSET_UNIVERSE_REQUIRED = ("BTCUSD", "USDKRW", "XAUUSD")
ASSET_UNIVERSE_COLUMN_ALIAS: Dict[str, str] = {
    "close": "NASDAQ_DLY_IXIC",
}


def _infer_series_and_interval(path: Path) -> Tuple[Optional[str], Optional[str]]:
    name = path.name.split(",")[0]
    series_id = None
    for key, val in FILENAME_SERIES_MAP.items():
        if key in name:
            series_id = val
            break
    interval = None
    if "1D" in path.name:
        interval = "1D"
    elif "1W" in path.name:
        interval = "1W"
    elif "1M" in path.name:
        interval = "1M"
    return series_id, interval


def _normalize_time(value: int) -> int:
    return value if value > 10_000_000_000 else value * 1000


def _parse_time_to_ms(raw: str) -> int:
    raw = str(raw).strip()
    # date string (e.g., 2015-04-20)
    if "-" in raw and ":" not in raw:
        dt = datetime.fromisoformat(raw)
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    # ISO with time
    if "T" in raw:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    # numeric
    try:
        val = float(raw)
        return _normalize_time(int(val))
    except Exception:
        raise ValueError(f"cannot parse time value: {raw}")


def backfill(data_dir: Path) -> None:
    settings = get_settings() if get_settings else None
    db_path = settings.db_path if settings else "./warroom.db"
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    with db.db_session(db_path) as conn:
        db.init_db(conn)
        for csv_file in data_dir.glob("*.csv"):
            series_id, interval = _infer_series_and_interval(csv_file)
            with csv_file.open(newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if rows and "time" in rows[0] and any(k in rows[0] for k in ASSET_UNIVERSE_REQUIRED):
                batch = []
                for row in rows:
                    raw_time = row.get("time")
                    if not raw_time:
                        continue
                    try:
                        ts_ms = _parse_time_to_ms(raw_time)
                    except Exception:
                        continue
                    for col, raw_val in row.items():
                        if col == "time" or raw_val in (None, ""):
                            continue
                        series = ASSET_UNIVERSE_COLUMN_ALIAS.get(col, col)
                        try:
                            value = float(raw_val)
                        except Exception:
                            continue
                        payload = {"time": raw_time, "value": value, "column": col}
                        batch.append((series, ts_ms, value, payload))
                if batch:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO market_observations
                        (series_id, time_utc_ms, interval, value, received_at, payload_json)
                        VALUES (?, ?, ?, ?, ?, json(?));
                        """,
                        [(sid, ts, "1D", val, now_iso, json.dumps(payload, ensure_ascii=False)) for (sid, ts, val, payload) in batch],
                    )
                    conn.commit()
                    print(f"ingested asset universe {csv_file.name} ({len(batch)} rows)")
                continue

            if not series_id or not interval:
                print(f"skip unknown file {csv_file.name}")
                continue

            if series_id == "COMBINED":
                if not rows or "time" not in rows[0]:
                    print(f"skip malformed combined file {csv_file.name}")
                    continue
                for col, sid in COMBINED_COLUMN_MAP.items():
                    # Only ingest daily close for trend/MA200 to avoid mixing 1D/1W/1M into one series_id.
                    if sid == "NASDAQ_DLY_IXIC" and interval != "1D":
                        continue
                    batch = []
                    for row in rows:
                        if not row.get(col):
                            continue
                        try:
                            ts_ms = _parse_time_to_ms(row["time"])
                            val = float(row[col])
                        except Exception:
                            continue
                        col_interval = COLUMN_INTERVAL_OVERRIDE.get(sid, interval)
                        batch.append(
                            (
                                sid,
                                ts_ms,
                                col_interval,
                                val,
                                now_iso,
                                {"time": ts_ms, "value": val},
                            )
                        )
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO market_observations
                        (series_id, time_utc_ms, interval, value, received_at, payload_json)
                        VALUES (?, ?, ?, ?, ?, json(?));
                        """,
                        [
                            (sid, ts_ms, interval, val, now_iso, json.dumps(payload, ensure_ascii=False))
                            for (sid, ts_ms, interval, val, now_iso, payload) in batch
                        ],
                    )
                    conn.commit()
                    count = len(batch)
                    print(f"ingested {csv_file.name} -> {sid} ({count} rows)")
                continue

            if not rows or "time" not in rows[0] or "close" not in rows[0]:
                print(f"skip malformed file {csv_file.name}")
                continue
            batch = []
            for row in rows:
                if row.get("close") in ("", None):
                    continue
                try:
                    ts_ms = _parse_time_to_ms(row["time"])
                    value = float(row["close"])
                except Exception:
                    continue
                payload = {k: v for k, v in row.items() if v not in (None, "")}
                batch.append((series_id, ts_ms, interval, value, now_iso, payload))
            conn.executemany(
                """
                INSERT OR REPLACE INTO market_observations
                (series_id, time_utc_ms, interval, value, received_at, payload_json)
                VALUES (?, ?, ?, ?, ?, json(?));
                """,
                [
                    (sid, ts, interval, val, now_iso, json.dumps(payload, ensure_ascii=False))
                    for (sid, ts, interval, val, now_iso, payload) in batch
                ],
            )
            conn.commit()
            count = len(batch)
            print(f"ingested {csv_file.name} -> {series_id} ({count} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill CSV data into warroom db")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("지표데이터"),
        help="directory containing CSV exports",
    )
    args = parser.parse_args()
    backfill(args.data_dir)


if __name__ == "__main__":
    main()
