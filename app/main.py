import logging
import sqlite3
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Set, Optional

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

if SITE_DIR.exists():
    app.mount("/site", StaticFiles(directory=SITE_DIR, html=True), name="site")
if SITE2_DIR.exists():
    app.mount("/site2", StaticFiles(directory=SITE2_DIR, html=True), name="site2")
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
if INDICATOR_DIR.exists():
    app.mount("/지표데이터", StaticFiles(directory=INDICATOR_DIR), name="indicator-data")


def _get_trigger_ids() -> Set[str]:
    """대소문자 무시를 위해 모든 트리거 ID를 대문자로 정규화하여 반환"""
    cfg = str(getattr(settings, "auto_refresh_trigger_series", "") or "").strip().upper()
    if cfg:
        return {s.strip() for s in cfg.split(",") if s.strip()}
    # 기본값은 나스닥 (IXIC) 관련 별칭들을 포함하도록 유연하게 설정
    return {"NASDAQ_DLY_IXIC", "IXIC", "NASDAQ", "US100"}


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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "2.1"}


@app.post("/order")
@app.post("/webhook/{webhook_token}")
async def ingest(request: Request, background_tasks: BackgroundTasks, webhook_token: str = None):
    """
    웹훅 통합 수신 엔드포인트.
    수신 -> 즉시 저장 -> 비동기 계산(BackgroundTasks) 순으로 안전하게 처리합니다.
    """
    if webhook_token is not None and webhook_token != settings.webhook_token:
        raise HTTPException(status_code=401, detail="invalid webhook token")

    raw_body = await request.body()
    text_body = raw_body.decode("utf-8", errors="ignore")
    data = _load_json_loose(text_body)
    
    if data is None:
        return JSONResponse({"detail": "invalid json"}, status_code=422)
        
    try:
        payload = TradingViewPayload(**data)
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors()}, status_code=422)

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

    # 2. 트리거 판정 (유연한 매칭 로직)
    input_id = payload.series_id.upper()
    trigger_set = _get_trigger_ids()
    
    is_trigger = input_id in trigger_set or any(kw in input_id for kw in ["IXIC", "NASDAQ"])

    if is_trigger:
        # 3. 비동기 작업 지시 (서버 응답은 즉시 반환)
        logger.info(f"Triggering background update for series: {input_id}")
        background_tasks.add_task(run_daily_job)

    return {"status": "ok", "ingested": input_id, "background_job": is_trigger}


@app.get("/")
def root() -> dict:
    return {
        "message": "WarRoom v2.1 Institutional Ingest Server",
        "uptime": datetime.now(tz=timezone.utc).isoformat(),
    }
