import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Any


def get_connection(db_path: str) -> sqlite3.Connection:
    """
    고성능 및 동시성 처리를 위해 설정된 DB 연결을 반환합니다.
    - WAL 모드: 읽기와 쓰기가 서로를 차단하지 않음
    - Timeout: 30초 설정으로 작업 경합 시 대기 유도
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Institutional Choice: 고가용성을 위한 WAL 모드 활성화
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


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


def insert_observation(
    conn: sqlite3.Connection,
    series_id: str,
    time_utc_ms: int,
    interval: str,
    value: float,
    received_at: str,
    payload: Dict[str, Any],
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO market_observations
        (series_id, time_utc_ms, interval, value, received_at, payload_json)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (
            series_id,
            time_utc_ms,
            interval,
            value,
            received_at,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    conn.commit()


def insert_daily_state(
    conn: sqlite3.Connection,
    as_of_date: str,
    state: str,
    score: Optional[float],
    reasons: Dict[str, Any],
    health: Dict[str, Any],
    created_at: str,
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO daily_states
        (as_of_date, state, score, reasons_json, health_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (
            as_of_date,
            state,
            score,
            json.dumps(reasons, ensure_ascii=False),
            json.dumps(health, ensure_ascii=False),
            created_at,
        ),
    )
    conn.commit()


def fetch_recent_states(
    conn: sqlite3.Connection, limit: int = 30
) -> List[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM daily_states
        ORDER BY as_of_date DESC
        LIMIT ?;
        """,
        (limit,),
    )
    return cur.fetchall()


def fetch_recent_states_upto(
    conn: sqlite3.Connection, as_of_date: str, limit: int = 30
) -> List[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM daily_states
        WHERE as_of_date <= ?
        ORDER BY as_of_date DESC
        LIMIT ?;
        """,
        (as_of_date, limit),
    )
    return cur.fetchall()


def fetch_observations(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT series_id, time_utc_ms, interval, value
        FROM market_observations;
        """
    )
    return cur.fetchall()


def fetch_observations_for_series(
    conn: sqlite3.Connection, series_ids: Iterable[str], interval: Optional[str] = None
) -> List[sqlite3.Row]:
    ids = [s for s in series_ids if s]
    if not ids:
        return []
    placeholders = ", ".join(["?"] * len(ids))
    params: List[Any] = list(ids)
    query = f"""
        SELECT series_id, time_utc_ms, interval, value
        FROM market_observations
        WHERE series_id IN ({placeholders})
    """
    if interval:
        query += " AND interval = ?"
        params.append(interval)
    cur = conn.cursor()
    cur.execute(query, params)
    return cur.fetchall()


@contextmanager
def db_session(db_path: str):
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()
