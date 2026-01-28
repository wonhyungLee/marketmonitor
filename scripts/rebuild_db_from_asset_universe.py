import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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

    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)
        cur = conn.cursor()
        batch = []
        total = 0
        with CSV_PATH.open(newline="") as f:
            reader = csv.DictReader(f)
            if "date" not in (reader.fieldnames or []):
                raise ValueError("asset_universe.csv must contain 'date' column")
            for row in reader:
                date_str = (row.get("date") or "").strip()
                if not date_str:
                    continue
                try:
                    dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                ts_ms = int(dt.timestamp() * 1000)
                received_at = dt.isoformat()

                # Normalize FXCM_COPPER -> COPPER (prefer explicit COPPER if present)
                copper_raw = row.get("COPPER")
                if copper_raw in (None, ""):
                    copper_raw = row.get("FXCM_COPPER")
                if copper_raw not in (None, ""):
                    try:
                        val = float(copper_raw)
                        payload = json.dumps({"source": "asset_universe_csv", "column": "COPPER"}, ensure_ascii=False)
                        batch.append(("COPPER", ts_ms, "1D", val, received_at, payload))
                    except Exception:
                        pass

                for key, raw in row.items():
                    if key in ("date", "FXCM_COPPER", "COPPER"):
                        continue
                    if raw in (None, ""):
                        continue
                    try:
                        val = float(raw)
                    except Exception:
                        continue
                    payload = json.dumps({"source": "asset_universe_csv", "column": key}, ensure_ascii=False)
                    batch.append((key, ts_ms, "1D", val, received_at, payload))
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
