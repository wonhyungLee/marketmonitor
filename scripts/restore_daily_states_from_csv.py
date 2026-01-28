import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _to_float(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    try:
        return float(s)
    except Exception:
        return None


def _to_int(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def restore(csv_path: Path, db_path: Path) -> int:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    rows = []
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            as_of_date = (row.get("as_of_date") or "").strip()
            if not as_of_date:
                continue
            state = (row.get("state") or "WARMUP").strip()
            score = _to_float(row.get("score"))

            reasons = {}
            triggers = row.get("triggers")
            if triggers not in (None, ""):
                reasons["triggers"] = triggers

            alloc = {}
            action = row.get("action")
            if action not in (None, ""):
                alloc["action"] = action
            eq = _to_float(row.get("equity_weight"))
            if eq is not None:
                alloc["equity_weight"] = eq
            if alloc:
                reasons["allocation"] = alloc

            trend = {}
            trend_signal = row.get("trend_signal")
            if trend_signal not in (None, ""):
                trend["signal"] = trend_signal
            trend_price = _to_float(row.get("trend_price"))
            if trend_price is not None:
                trend["price"] = trend_price
            trend_ma = _to_float(row.get("trend_ma"))
            if trend_ma is not None:
                trend["ma"] = trend_ma
            if trend:
                reasons["trend"] = trend

            streaks = {}
            for key in ("streak_ge_2", "streak_ge_3_5", "streak_lt_2", "streak_le_3"):
                val = _to_int(row.get(key))
                if val is not None:
                    streaks[key] = val
            if streaks:
                reasons["streaks"] = streaks

            components = {}
            for key in (
                "T10Y2Y_cross_up",
                "BAML_spread_risk",
                "WEI_recession_trend",
                "COPPER_GOLD_under_ma200",
                "UMCSENT_low",
            ):
                val = _to_float(row.get(key))
                if val is not None:
                    components[key] = val
            if components:
                reasons["components"] = components

            hard = row.get("hard_defcon1")
            if hard not in (None, ""):
                try:
                    reasons["hard_defcon1"] = bool(int(float(hard)))
                except Exception:
                    pass

            prev_state = row.get("prev_state")
            if prev_state not in (None, ""):
                reasons["prev_state"] = prev_state

            if score is not None:
                reasons.setdefault("raw_score", score)

            health = {"source": "csv_restore"}

            rows.append(
                (
                    as_of_date,
                    state,
                    score,
                    json.dumps(reasons, ensure_ascii=False),
                    json.dumps(health, ensure_ascii=False),
                    now_iso,
                )
            )

    if not rows:
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
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

        conn.execute("DELETE FROM daily_states;")
        conn.commit()

        conn.executemany(
            """
            INSERT OR REPLACE INTO daily_states
            (as_of_date, state, score, reasons_json, health_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()

    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore daily_states table from market_states_daily.csv")
    parser.add_argument("--csv", type=Path, default=Path("data/market_states_daily.csv"))
    parser.add_argument("--db", type=Path, default=Path("warroom.db"))
    args = parser.parse_args()

    count = restore(args.csv, args.db)
    print(f"restored {count} rows into {args.db}")


if __name__ == "__main__":
    main()
