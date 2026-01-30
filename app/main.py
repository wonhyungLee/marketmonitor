import logging
import sqlite3
import os
import csv
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Set, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import db
from app.models import TradingViewPayload, _load_json_loose
from app.settings import get_settings
from scripts.run_daily import run_daily_job # 모듈화된 핵심 로직 임포트


settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("warroom")

app = FastAPI(title="WarRoom v2.1 - Institutional", version="2.1")

BASE_DIR = Path(__file__).resolve().parent.parent
SITE_DIR = BASE_DIR / "site"
DATA_DIR = BASE_DIR / "data"
SITE2_DIR = BASE_DIR / "site-react" / "dist"
INDICATOR_DIR = BASE_DIR / "지표데이터"
WONGRAM_DIR = BASE_DIR.parent / "html2" / "public"
WEBHOOK_CSV = DATA_DIR / "webhook_records.csv"

if SITE_DIR.exists():
    app.mount("/site", StaticFiles(directory=SITE_DIR, html=True), name="site")
if SITE2_DIR.exists():
    app.mount("/site2", StaticFiles(directory=SITE2_DIR, html=True), name="site2")
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
if INDICATOR_DIR.exists():
    app.mount("/지표데이터", StaticFiles(directory=INDICATOR_DIR), name="indicator-data")

_scheduler_started = False


def _get_trigger_ids() -> Set[str]:
    """대소문자 무시를 위해 모든 트리거 ID를 대문자로 정규화하여 반환"""
    cfg = str(getattr(settings, "auto_refresh_trigger_series", "") or "").strip().upper()
    if not cfg or cfg in {"*", "ALL"}:
        # Empty or wildcard means: trigger on any webhook.
        return set()
    return {s.strip() for s in cfg.split(",") if s.strip()}


def _get_allowed_webhook_series() -> Set[str]:
    """웹훅 저장 허용 시리즈 ID 목록 (대소문자 무시). 빈 값이면 모두 허용."""
    cfg = str(getattr(settings, "webhook_allowed_series", "") or "").strip().upper()
    if not cfg:
        return set()
    return {s.strip() for s in cfg.split(",") if s.strip()}


def _write_webhook_records_csv(conn) -> None:
    try:
        rows = conn.execute(
            """
            SELECT series_id, time_utc_ms, interval, value, received_at
            FROM market_observations
            ORDER BY received_at ASC
            """
        ).fetchall()

        WEBHOOK_CSV.parent.mkdir(parents=True, exist_ok=True)
        with WEBHOOK_CSV.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["series_id", "time_utc_ms", "interval", "value", "received_at"])
            writer.writerows(rows)
    except Exception:
        logger.exception("Failed to write webhook_records.csv")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body_bytes = await request.body()
    body_text = body_bytes.decode(errors="ignore")
    logger.error(f"Validation error: {exc.errors()} Body: {body_text}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": body_text},
    )


@app.on_event("startup")
def startup() -> None:
    with db.db_session(settings.db_path) as conn:
        db.init_db(conn)
    logger.info("WarRoom v2.1 Engine Online. Database ready.")
    _start_periodic_daily_job()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "2.1"}


def _start_periodic_daily_job() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    if not getattr(settings, "auto_refresh_daily", False):
        logger.info("Periodic daily job disabled (AUTO_REFRESH_DAILY=false)")
        return

    try:
        interval = int(getattr(settings, "auto_refresh_daily_interval_sec", 10800))
    except Exception:
        interval = 10800
    if interval < 60:
        interval = 60

    try:
        window_days = int(getattr(settings, "auto_refresh_window_days", 7))
    except Exception:
        window_days = 7

    def _loop() -> None:
        logger.info(
            "Periodic daily job thread started. interval=%ss window_days=%s",
            interval,
            window_days,
        )
        while True:
            try:
                run_daily_job(window_days=window_days, force=True, trigger="timer")
            except Exception:
                logger.exception("Periodic daily job failed")
            time.sleep(interval)

    thread = threading.Thread(target=_loop, name="warroom-daily-loop", daemon=True)
    thread.start()
    _scheduler_started = True


@app.get("/order")
@app.post("/order")
@app.get("/webhook")
@app.post("/webhook")
@app.get("/webhook/{webhook_token}")
@app.post("/webhook/{webhook_token}")
async def ingest(request: Request, background_tasks: BackgroundTasks, webhook_token: str = None):
    """
    웹훅 통합 수신 엔드포인트.
    수신 -> 즉시 저장 -> 비동기 계산(BackgroundTasks) 순으로 안전하게 처리합니다.
    """
    raw_body = await request.body()
    text_body = raw_body.decode("utf-8", errors="ignore") if raw_body else ""
    data = _load_json_loose(text_body) if text_body.strip() else None

    if data is None:
        qp = dict(request.query_params)
        payload_raw = None
        for key in ("payload", "json", "data", "body"):
            if key in qp and qp.get(key):
                payload_raw = qp.get(key)
                break
        if payload_raw:
            data = _load_json_loose(payload_raw)
        elif qp:
            data = qp

    if data is None:
        logger.error(
            "Invalid JSON payload. method=%s path=%s body=%s query=%s",
            request.method,
            request.url.path,
            text_body,
            dict(request.query_params),
        )
        return JSONResponse({"detail": "invalid json"}, status_code=422)
        
    try:
        payload = TradingViewPayload(**data)
    except ValidationError as exc:
        logger.error("Webhook payload validation failed: %s body=%s", exc, text_body)
        return JSONResponse({"detail": exc.errors()}, status_code=422)

    allowed_series = _get_allowed_webhook_series()
    input_id = payload.series_id.upper()
    allow_match = bool(getattr(settings, "webhook_allow_payload_match", False))
    payload_allowed = (not allowed_series) or (input_id in allowed_series)

    if webhook_token is not None and settings.webhook_token and webhook_token != settings.webhook_token:
        if not (allow_match and payload_allowed):
            raise HTTPException(status_code=401, detail="invalid webhook token")
        logger.warning("Token mismatch but payload allowed; accepting. series_id=%s", input_id)

    if allow_match and not payload_allowed:
        logger.warning("Webhook payload series not allowed; skipping. series_id=%s", input_id)
        return {"status": "ignored", "ingested": input_id, "reason": "series_id_not_allowed"}

    # 1. 원본 데이터 DB 저장 (WAL 모드로 락 걱정 없음)
    with db.db_session(settings.db_path) as conn:
        db.insert_observation(
            conn=conn,
            series_id=payload.series_id,
            time_utc_ms=payload.time_utc_ms,
            interval=payload.interval,
            value=payload.value,
            received_at=payload.received_at_iso,
            payload=payload.dict(),
        )
        _write_webhook_records_csv(conn)

    # 2. 트리거 판정 (유연한 매칭 로직)
    trigger_set = _get_trigger_ids()

    # 키워드 매칭 및 명시적 리스트 매칭 결합
    if not getattr(settings, "auto_refresh_on_webhook", False):
        is_trigger = False
    elif not trigger_set:
        is_trigger = True
    else:
        is_trigger = input_id in trigger_set or any(kw in input_id for kw in ["IXIC", "NASDAQ", "BTC", "COPPER"])

    if is_trigger:
        # 3. 비동기 작업 지시 (서버 응답은 즉시 반환)
        logger.info(f"Triggering background update for series: {input_id}")
        window_days = int(getattr(settings, "auto_refresh_window_days", 7) or 7)
        min_interval_sec = int(getattr(settings, "auto_refresh_daily_min_interval_sec", 0) or 0)
        background_tasks.add_task(
            run_daily_job,
            window_days=window_days,
            min_interval_sec=min_interval_sec if min_interval_sec > 0 else None,
            trigger="webhook",
        )
    else:
        logger.info(f"Ingested {input_id}, but not a trigger. Skipping background job.")

    return {"status": "ok", "ingested": input_id, "background_job": is_trigger}


if WONGRAM_DIR.exists():
    # Register last so API routes win on exact matches like /health, /order.
    app.mount("/", StaticFiles(directory=WONGRAM_DIR, html=True), name="wongram-root")
