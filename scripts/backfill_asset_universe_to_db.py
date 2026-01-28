import json
import sqlite3
from datetime import timezone
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "warroom.db"
CSV_PATH = BASE_DIR / "data" / "asset_universe.csv"


def init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS market_observations (
            series_id TEXT NOT NULL,
            time_utc_ms INTEGER NOT NULL,
            interval TEXT NOT NULL,
            value REAL NOT NULL,
            received_at TEXT NOT NULL,
            payload_json TEXT,
            PRIMARY KEY (series_id, time_utc_ms, interval)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_states (
            as_of_date TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            score REAL NULL,
            reasons_json TEXT,
            health_json TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def main() -> None:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"asset_universe.csv not found: {CSV_PATH}")

    df = pd.read_csv(CSV_PATH)
    if "date" not in df.columns:
        raise ValueError("asset_universe.csv must contain 'date' column")

    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df = df.dropna(subset=["date"])

    value_cols = [c for c in df.columns if c != "date"]
    if not value_cols:
        raise ValueError("asset_universe.csv has no series columns")

    long_df = df.melt(id_vars=["date"], value_vars=value_cols, var_name="series_id", value_name="value")
    long_df = long_df.dropna(subset=["value"])

    long_df["time_utc_ms"] = (long_df["date"].dt.floor("D").astype("int64") // 1_000_000)
    long_df["received_at"] = long_df["date"].dt.tz_convert(timezone.utc).dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)
        cur = conn.cursor()
        batch = []
        total = 0
        for row in long_df.itertuples(index=False):
            payload = json.dumps({"source": "asset_universe_csv", "column": row.series_id}, ensure_ascii=False)
            batch.append((row.series_id, int(row.time_utc_ms), "1D", float(row.value), row.received_at, payload))
            if len(batch) >= 5000:
                cur.executemany(
                    """
                    INSERT OR REPLACE INTO market_observations
                    (series_id, time_utc_ms, interval, value, received_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    batch,
                )
                conn.commit()
                total += len(batch)
                batch = []
        if batch:
            cur.executemany(
                """
                INSERT OR REPLACE INTO market_observations
                (series_id, time_utc_ms, interval, value, received_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            conn.commit()
            total += len(batch)
        print(f"Inserted/updated {total} rows into {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
