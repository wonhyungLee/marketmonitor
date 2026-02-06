"""
Recompute (or resume) the `daily_states` table over the full history.

Key requirements (per 운영 요청):
- Read `market_observations` from DB into a pandas DataFrame.
- Filter to required indicator series only.
- Compute `as_of_date` in UTC.
- Long runs must be restartable (resume from MAX(as_of_date)+1).
- Portfolio recommendation is unnecessary during backfill: disable it.

This script is intentionally conservative:
- Default mode is RESUME (never deletes existing rows unless --reset is provided).
- Inserts are committed by the engine per-day, so progress is durable for resume.
"""

from __future__ import annotations

import argparse
import os
import sys
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

# Ensure repo root is on sys.path when executed as a file under ./scripts.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Keep everything UTC for this job (env vars override .env).
os.environ.setdefault("TIMEZONE", "UTC")
os.environ.setdefault("AS_OF_TZ", "UTC")

import numpy as np
import pandas as pd

from app import db

# Import after UTC env is pinned.
import app.engine as engine_mod
import app.settings as settings_mod
import app.snapshot as snapshot_mod


SERIES_IDS = (
    "T10Y2Y",
    "BAMLH0A0HYM2",
    "COPPER_GOLD_RATIO",
    "WEI",
    "SAHMREALTIME",
    "UMCSENT",
    "NASDAQ_DLY_IXIC",
)


@dataclass(frozen=True)
class _SeriesCache:
    df: pd.DataFrame
    as_of_ord: np.ndarray  # monotonic non-decreasing (timestamp-sorted, UTC date derived)


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw.strip())


def _fetch_daily_states_stats(conn: sqlite3.Connection) -> Tuple[int, Optional[date], Optional[date]]:
    row = conn.execute("SELECT COUNT(*), MIN(as_of_date), MAX(as_of_date) FROM daily_states;").fetchone()
    if not row:
        return 0, None, None
    count = int(row[0] or 0)
    min_s = row[1]
    max_s = row[2]
    return count, (_parse_date(min_s) if min_s else None), (_parse_date(max_s) if max_s else None)


def _load_observations_frame(conn: sqlite3.Connection) -> pd.DataFrame:
    placeholders = ", ".join(["?"] * len(SERIES_IDS))
    query = f"""
        SELECT series_id, time_utc_ms, interval, value
        FROM market_observations
        WHERE series_id IN ({placeholders});
    """
    df = pd.read_sql_query(query, conn, params=list(SERIES_IDS))
    if df.empty:
        return df

    # UTC timestamps + UTC date boundary.
    df["timestamp"] = pd.to_datetime(df["time_utc_ms"], unit="ms", utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df["as_of_date"] = df["timestamp"].dt.date

    # Sorting once lets us do fast slicing in the patched _series_frame.
    df = df.sort_values(["series_id", "timestamp"], kind="mergesort").reset_index(drop=True)
    return df


def _build_series_cache(df: pd.DataFrame) -> Dict[str, _SeriesCache]:
    cache: Dict[str, _SeriesCache] = {}
    for sid in sorted(set(str(s) for s in df["series_id"].unique())):
        s_df = df[df["series_id"] == sid].reset_index(drop=True)
        if s_df.empty:
            continue
        # Convert datetime.date -> ordinal for fast searchsorted
        ords = np.fromiter((d.toordinal() for d in s_df["as_of_date"]), dtype=np.int32, count=len(s_df))
        cache[sid] = _SeriesCache(df=s_df, as_of_ord=ords)
    return cache


def _install_cached_series_frame(series_cache: Dict[str, _SeriesCache], template_columns) -> None:
    empty_template = pd.DataFrame(columns=list(template_columns))

    def _series_frame_cached(_df_unused: pd.DataFrame, series_id: str, as_of_date: date, tz) -> pd.DataFrame:
        cached = series_cache.get(series_id)
        if cached is None:
            return empty_template

        target = int(as_of_date.toordinal())
        # Right side: include rows where as_of_date == target day.
        idx = int(np.searchsorted(cached.as_of_ord, target, side="right"))
        if idx <= 0:
            return cached.df.iloc[0:0]
        return cached.df.iloc[:idx]

    snapshot_mod._series_frame = _series_frame_cached  # type: ignore[attr-defined]


def main() -> None:
    ap = argparse.ArgumentParser(description="Recompute/resume daily_states over full history (UTC).")
    ap.add_argument("--db-path", default="warroom.db", help="Path to warroom.db")
    ap.add_argument("--start-date", default="1970-01-01", help="Start date (used on --reset), YYYY-MM-DD")
    ap.add_argument("--end-date", default="", help="Optional end date YYYY-MM-DD (default: max obs date)")
    ap.add_argument("--reset", action="store_true", help="DELETE daily_states and recompute from --start-date")
    ap.add_argument("--log-every", type=int, default=200, help="Log every N days")
    args = ap.parse_args()

    # Ensure settings reflect the UTC env vars.
    try:
        settings_mod.get_settings.cache_clear()
    except Exception:
        pass

    # Disable portfolio recommendation during backfill (performance).
    engine_mod.recommend_portfolio = lambda *a, **k: None  # type: ignore[assignment]

    with db.db_session(args.db_path) as conn:
        if bool(args.reset):
            print("Reset requested: deleting daily_states ...")
            conn.execute("DELETE FROM daily_states;")
            conn.commit()

        # 1) Snapshot current daily_states status (resume checkpoint).
        row_count, min_d, max_d = _fetch_daily_states_stats(conn)
        print(
            f"daily_states: row_count={row_count:,} min={min_d.isoformat() if min_d else None} "
            f"max={max_d.isoformat() if max_d else None}"
        )

        # 2) Load observations (filtered) and determine processing range (UTC).
        t_load = time.time()
        obs_df = _load_observations_frame(conn)
        if obs_df.empty:
            raise SystemExit("No observations found for required series.")
        data_max: date = max(obs_df["as_of_date"])
        print(f"loaded observations: {len(obs_df):,} rows ({time.time() - t_load:.1f}s), data_max={data_max.isoformat()}")

        start_d = _parse_date(args.start_date)
        end_d = _parse_date(args.end_date) if str(args.end_date).strip() else data_max
        if end_d > data_max:
            end_d = data_max
        if end_d < start_d:
            raise SystemExit("end-date must be >= start-date")

        # Resume logic:
        # - If rows exist, ensure it's contiguous from min..max (otherwise require --reset).
        # - Continue from (max + 1 day).
        if row_count > 0 and min_d and max_d:
            expected = (max_d - min_d).days + 1
            if expected != row_count:
                raise SystemExit(
                    "daily_states appears to have gaps (count != date_range). "
                    "Please backup then run with --reset."
                )
            cur = max_d + timedelta(days=1)
        else:
            cur = start_d

        if cur < start_d:
            # In case the table contains data earlier than requested start-date.
            cur = start_d

        if cur > end_d:
            print(f"Nothing to do (resume_start={cur.isoformat()} > end_date={end_d.isoformat()}).")
            return

        # Performance: patch snapshot._series_frame to avoid scanning the whole DF per day.
        series_cache = _build_series_cache(obs_df)
        _install_cached_series_frame(series_cache, template_columns=obs_df.columns)

        print(f"recompute range: {cur.isoformat()} -> {end_d.isoformat()} (UTC, { (end_d-cur).days + 1:,} days)")

        t0 = time.time()
        n = 0
        last_result_state = ""

        while cur <= end_d:
            # Retry on transient DB locks (concurrent jobs / WAL contention).
            res = None
            for attempt in range(10):
                try:
                    res = engine_mod.evaluate(conn, as_of_date=cur, data_frame=obs_df)
                    break
                except sqlite3.OperationalError as exc:
                    msg = str(exc).lower()
                    if "database is locked" in msg or "database table is locked" in msg or "locked" in msg:
                        time.sleep(min(5.0, 0.25 * (attempt + 1)))
                        continue
                    raise
            if res is None:
                raise SystemExit(f"Failed to evaluate {cur.isoformat()} due to repeated DB locks.")

            n += 1
            last_result_state = res.state

            if n == 1 or (args.log_every and n % int(args.log_every) == 0) or cur == end_d:
                elapsed = max(1e-6, time.time() - t0)
                rate = n / elapsed
                remaining = (end_d - cur).days
                eta_sec = remaining / rate if rate > 0 else float("inf")
                print(
                    f"{cur.isoformat()} state={res.state} score={res.score} "
                    f"({n:,} days, {rate:.2f} d/s, eta~{eta_sec/60:.1f}m)"
                )

            cur += timedelta(days=1)

        # Final status
        row_count2, min_d2, max_d2 = _fetch_daily_states_stats(conn)
        print(
            f"done. daily_states: row_count={row_count2:,} min={min_d2.isoformat() if min_d2 else None} "
            f"max={max_d2.isoformat() if max_d2 else None} last_state={last_result_state}"
        )


if __name__ == "__main__":
    main()
