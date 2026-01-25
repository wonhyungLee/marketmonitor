import logging
import sqlite3
import csv
import subprocess
import sys
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import db
from app.models import TradingViewPayload, _load_json_loose
from app.settings import get_settings


settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("warroom")

app = FastAPI(title="WarRoom v2.0", version="2.0")

BASE_DIR = Path(__file__).resolve().parent.parent
SITE_DIR = BASE_DIR / "site"
DATA_DIR = BASE_DIR / "data"
SITE2_DIR = BASE_DIR / "site-react" / "dist"
INDICATOR_DIR = BASE_DIR / "지표데이터"

_refresh_state_lock = threading.Lock()
_refresh_inflight = False
_last_refresh_epoch = 0.0

_daily_refresh_lock = threading.Lock()
_daily_refresh_inflight = False
_last_daily_refresh_epoch = 0.0

if SITE_DIR.exists():
    app.mount("/site", StaticFiles(directory=SITE_DIR, html=True), name="site")
if SITE2_DIR.exists():
    app.mount("/site2", StaticFiles(directory=SITE2_DIR, html=True), name="site2")
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
if INDICATOR_DIR.exists():
    app.mount("/지표데이터", StaticFiles(directory=INDICATOR_DIR), name="indicator-data")


def _run_portfolio_refresh() -> None:
    global _refresh_inflight
    try:
        command = [
            sys.executable,
            str(BASE_DIR / "scripts" / "build_portfolio_daily.py"),
            "--use-db",
            "--db-path",
            str(Path(settings.db_path)),
            "--states-csv",
            str(DATA_DIR / "market_states_daily.csv"),
            "--out-csv",
            str(DATA_DIR / "portfolio_daily.csv"),
        ]
        result = subprocess.run(
            command,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(
                "portfolio refresh failed rc=%s stdout=%s stderr=%s",
                result.returncode,
                result.stdout.strip(),
                result.stderr.strip(),
            )
        else:
            logger.info("portfolio refresh ok")
    except Exception:
        logger.exception("portfolio refresh error")
    finally:
        with _refresh_state_lock:
            _refresh_inflight = False


def _refresh_trigger_series_ids() -> set[str]:
    cfg = str(getattr(settings, "auto_refresh_trigger_series", "") or "").strip()
    if cfg:
        ids = {s.strip() for s in cfg.split(",") if s.strip()}
        return {s for s in ids if s}
    if settings.trend_series_id:
        return {str(settings.trend_series_id).strip()}
    return set()


def _schedule_portfolio_refresh(series_id: str) -> bool:
    global _refresh_inflight, _last_refresh_epoch
    if not settings.auto_refresh_portfolio:
        return False
    if series_id not in _refresh_trigger_series_ids():
        return False
    now_epoch = time.time()
    min_interval = settings.auto_refresh_min_interval_sec
    with _refresh_state_lock:
        if _refresh_inflight:
            return False
        if now_epoch - _last_refresh_epoch < min_interval:
            return False
        _refresh_inflight = True
        _last_refresh_epoch = now_epoch
    threading.Thread(target=_run_portfolio_refresh, daemon=True).start()
    return True


def _run_daily_refresh() -> None:
    global _daily_refresh_inflight
    try:
        command = [
            sys.executable,
            str(BASE_DIR / "scripts" / "run_daily.py"),
        ]
        result = subprocess.run(
            command,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(
                "daily refresh failed rc=%s stdout=%s stderr=%s",
                result.returncode,
                result.stdout.strip(),
                result.stderr.strip(),
            )
        else:
            logger.info("daily refresh ok")
    except Exception:
        logger.exception("daily refresh error")
    finally:
        with _daily_refresh_lock:
            _daily_refresh_inflight = False


def _schedule_daily_refresh(series_id: str) -> bool:
    global _daily_refresh_inflight, _last_daily_refresh_epoch
    if not settings.auto_refresh_daily:
        return False
    if series_id not in _refresh_trigger_series_ids():
        return False
    now_epoch = time.time()
    min_interval = settings.auto_refresh_daily_min_interval_sec
    with _daily_refresh_lock:
        if _daily_refresh_inflight:
            return False
        if now_epoch - _last_daily_refresh_epoch < min_interval:
            return False
        _daily_refresh_inflight = True
        _last_daily_refresh_epoch = now_epoch
    threading.Thread(target=_run_daily_refresh, daemon=True).start()
    return True


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body_bytes = await request.body()
    body_text = body_bytes.decode(errors="ignore")
    client_host = request.client.host if request.client else "unknown"
    logger.error("validation error from %s: %s body=%s", client_host, exc.errors(), body_text)
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": body_text},
    )


@app.on_event("startup")
def startup() -> None:
    with db.db_session(settings.db_path) as conn:
        db.init_db(conn)
    logger.info("database ready at %s", settings.db_path)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/order")
@app.post("/webhook/{webhook_token}")
async def ingest(request: Request, webhook_token: str = None):
    if webhook_token is not None and webhook_token != settings.webhook_token:
        raise HTTPException(status_code=401, detail="invalid webhook token")

    raw_body = await request.body()
    text_body = raw_body.decode("utf-8", errors="ignore") if isinstance(raw_body, (bytes, bytearray)) else str(raw_body)
    data = _load_json_loose(text_body)
    if data is None:
        logger.error("failed to parse payload body=%s", text_body)
        return JSONResponse({"detail": "invalid payload", "body": text_body}, status_code=422)
    try:
        payload = TradingViewPayload(**data)
    except ValidationError as exc:
        logger.error("validation error: %s body=%s", exc.errors(), text_body)
        return JSONResponse({"detail": exc.errors(), "body": text_body}, status_code=422)

    record = {
        "series_id": payload.series_id,
        "time_utc_ms": payload.time_utc_ms,
        "interval": payload.interval,
        "value": payload.value,
        "received_at": payload.received_at_iso,
        "payload": payload.dict(),
    }

    try:
        with db.db_session(settings.db_path) as conn:
            db.insert_observation(
                conn=conn,
                series_id=record["series_id"],
                time_utc_ms=record["time_utc_ms"],
                interval=record["interval"],
                value=record["value"],
                received_at=record["received_at"],
                payload=record["payload"],
            )
    except sqlite3.IntegrityError:
        logger.info("duplicate payload skipped: %s %s", record["series_id"], record["time_utc_ms"])
        return JSONResponse({"status": "duplicate"}, status_code=200)

    # Save to CSV (Real-time log)
    try:
        csv_path = DATA_DIR / "webhook_records.csv"
        file_exists = csv_path.exists()
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["series_id", "time_utc_ms", "interval", "value", "received_at"])
            writer.writerow([
                record["series_id"],
                record["time_utc_ms"],
                record["interval"],
                record["value"],
                record["received_at"]
            ])
    except Exception as e:
        logger.error("failed to write to csv: %s", e)

    daily_scheduled = _schedule_daily_refresh(record["series_id"])
    if not daily_scheduled:
        _schedule_portfolio_refresh(record["series_id"])

    logger.info(
        "ingested %s %s %s value=%s",
        record["series_id"],
        record["interval"],
        record["time_utc_ms"],
        record["value"],
    )
    return {"status": "ok"}


@app.get("/")
def root() -> dict:
    return {
        "message": "WarRoom v2.0 ingest",
        "uptime": datetime.now(tz=timezone.utc).isoformat(),
    }
