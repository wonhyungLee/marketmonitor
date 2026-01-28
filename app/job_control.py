from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover (non-POSIX)
    fcntl = None


BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "data" / ".job_state"
LOCK_DIR = BASE_DIR / "data" / ".job_lock"


def _state_path(job_name: str) -> Path:
    return STATE_DIR / f"{job_name}.json"


def _lock_path(job_name: str) -> Path:
    return LOCK_DIR / f"{job_name}.lock"


def read_last_run_ts(job_name: str) -> Optional[float]:
    path = _state_path(job_name)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        ts = raw.get("last_run_ts")
        return float(ts) if ts is not None else None
    except Exception:
        return None


def write_last_run_ts(job_name: str, ts: float) -> None:
    path = _state_path(job_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last_run_ts": float(ts)}), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def job_lock(job_name: str):
    lock_path = _lock_path(job_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            yield None
            return
    try:
        yield handle
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()
