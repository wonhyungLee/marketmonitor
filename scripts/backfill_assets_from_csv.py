"""Backfill a multi-asset CSV into the WarRoom SQLite DB.

Target input (by default): `지표데이터/투자자산모음.csv`

This is optional. The portfolio builder can also read the CSV directly,
but backfilling is useful if you want all assets in `warroom.db`.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
# Allow running from anywhere
sys.path.insert(0, str(BASE_DIR))

from app import db  # noqa: E402
from app.paths import find_indicator_dir  # noqa: E402
from app.settings import get_settings  # noqa: E402


def _read_csv_any_encoding(path: Path) -> pd.DataFrame:
    last_err: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as exc:
            last_err = exc
    if last_err:
        raise last_err
    return pd.read_csv(path)


def _resolve_db_path(raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (BASE_DIR / p)


def main() -> None:
    try:
        s = get_settings()
        db_path = _resolve_db_path(s.db_path)
    except Exception:
        # Allow running without .env (WEBHOOK_TOKEN is required in server settings).
        db_path = BASE_DIR / 'warroom.db'

    indicator_dir = find_indicator_dir(BASE_DIR)
    csv_path = indicator_dir / "투자자산모음.csv"
    if not csv_path.exists():
        print(f"Error: Asset CSV not found at {csv_path}")
        return

    print(f"Reading asset data from {csv_path}...")
    df = _read_csv_any_encoding(csv_path)

    if "time" not in df.columns:
        print("Error: CSV must have 'time' column")
        return

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time")

    # CSV prefix -> canonical series_id
    col_map = {
        "BTCUSD": "BTCUSD",
        "XAUUSD": "XAUUSD",
        "USDKRW": "USDKRW",
        "COPPER": "COPPER",
        "REMX": "REMX",
        "ALUMINUM": "ALUMINUM",
        "URANIUM": "URANIUM",
        # Yields
        "US10Y": "US10Y",
        "US02Y": "US02Y",
    }

    # Detect matching columns (handles cases like "BTCUSD · INDEX: close")
    target_cols: dict[str, str] = {}
    for col in df.columns:
        if col == "time":
            continue
        col_str = str(col)
        for prefix, sid in col_map.items():
            if col_str.startswith(prefix):
                target_cols[col_str] = sid
                break

    if not target_cols:
        print("No backfillable asset columns found in CSV.")
        return

    print(f"Found columns to backfill: {target_cols}")

    now_iso = datetime.now(tz=timezone.utc).isoformat()
    batch: list[tuple[str, int, str, float, str, str]] = []

    for _, row in df.iterrows():
        dt = row["time"].to_pydatetime()
        # Use 00:00 UTC for the date.
        dt_utc = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
        ts_ms = int(dt_utc.timestamp() * 1000)

        for col, series_id in target_cols.items():
            val = row.get(col)
            if pd.isna(val):
                continue
            try:
                fval = float(val)
            except Exception:
                continue

            payload_json = json.dumps(
                {"source": "backfill_csv", "origin_col": col},
                ensure_ascii=False,
            )
            batch.append((series_id, ts_ms, "1D", fval, now_iso, payload_json))

    if not batch:
        print("Nothing to insert (all values empty/invalid).")
        return

    with db.db_session(str(db_path)) as conn:
        db.init_db(conn)
        conn.executemany(
            """
            INSERT OR REPLACE INTO market_observations
            (series_id, time_utc_ms, interval, value, received_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            batch,
        )
        conn.commit()

    print(f"Done! Inserted/Updated {len(batch)} records into {db_path}")


if __name__ == "__main__":
    main()
