import sys
from pathlib import Path
import pandas as pd
import sqlite3
import json

# Add project root to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from app import db, settings

def _read_csv_any_encoding(path: Path) -> pd.DataFrame:
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as exc:
            last_err = exc
    if last_err:
        raise last_err
    return pd.read_csv(path)

def main():
    s = settings.get_settings()
    db_path = BASE_DIR / s.db_path
    
    # Locate asset CSV
    csv_path = BASE_DIR / "지표데이터" / "투자자산모음.csv"
    if not csv_path.exists():
        print(f"Error: Asset CSV not found at {csv_path}")
        return

    print(f"Reading asset data from {csv_path}...")
    df = _read_csv_any_encoding(csv_path)
    
    # Normalize Date
    if "time" not in df.columns:
        print("Error: CSV must have 'time' column")
        return
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time")

    # Map CSV columns to DB series_ids
    # CSV Column -> DB Series ID
    col_map = {
        "BTCUSD": "BTCUSD",
        "XAUUSD": "XAUUSD",
        "USDKRW": "USDKRW",
        "COPPER": "COPPER",
        "REMX": "REMX",
        "ALUMINUM": "ALUMINUM",
        "URANIUM": "URANIUM",
        "US10Y": "US10Y", # Yields
        "US02Y": "US02Y",
    }
    
    # Find which columns actually exist in CSV
    target_cols = {}
    for col in df.columns:
        for key, sid in col_map.items():
            if str(col).startswith(key): # Handle prefixes like BTCUSD...
                target_cols[col] = sid
                break
    
    print(f"Found columns to backfill: {target_cols}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    total_inserted = 0
    
    for _, row in df.iterrows():
        dt = row["time"]
        # UTC midnight timestamp in ms
        ts_ms = int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        
        for col, series_id in target_cols.items():
            val = row[col]
            if pd.isna(val):
                continue
                
            val = float(val)
            
            # Insert into DB
            cursor.execute(
                """
                INSERT OR REPLACE INTO market_observations
                (series_id, time_utc_ms, interval, value, received_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    series_id,
                    ts_ms,
                    "1D",
                    val,
                    dt.isoformat(), # approximate received_at
                    json.dumps({"source": "backfill_csv", "origin_col": col})
                )
            )
            total_inserted += 1
            
    conn.commit()
    conn.close()
    
    print(f"Done! Inserted/Updated {total_inserted} records into {db_path}")

if __name__ == "__main__":
    main()
