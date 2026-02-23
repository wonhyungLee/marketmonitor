import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import db
from app.coupang_ads import build_trader_combo_payload
from app.models import TradingViewPayload, _load_json_loose
from app.paths import find_indicator_dir, repo_root
from app.settings import get_settings
from scripts.run_daily import run_daily_job  # core daily pipeline

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("warroom")

app = FastAPI(title="WarRoom v2.1 - Institutional", version="2.1")

BASE_DIR = repo_root()
SITE_DIR = BASE_DIR / "site"
DATA_DIR = BASE_DIR / "data"
PRIMARY_SITE2_DIR = Path("/home/ubuntu/html5/site2")
FALLBACK_SITE2_DIR = BASE_DIR / "site-react" / "dist"


def resolve_site2_dir() -> Path | None:
    for candidate in (PRIMARY_SITE2_DIR, FALLBACK_SITE2_DIR):
        if candidate.exists() and (candidate / "index.html").is_file():
            return candidate
    return None


SITE2_DIR = resolve_site2_dir()
INDICATOR_DIR = find_indicator_dir(BASE_DIR)


def _build_coupang_combo_response(
    theme: str | None = None,
    subId: str | None = None,
    category: str | None = "food",
) -> JSONResponse:
    try:
        payload = build_trader_combo_payload(theme=theme, sub_id=subId, category=category)
        return JSONResponse(payload)
    except Exception as exc:
        # Do not break page render on ad API errors.
        return JSONResponse({"ok": False, "items": [], "message": str(exc)})


if SITE_DIR.exists():
    app.mount("/site", StaticFiles(directory=SITE_DIR, html=True), name="site")
if SITE2_DIR is not None and SITE2_DIR.exists():
    site2_app = FastAPI(title="site2", docs_url=None, redoc_url=None, openapi_url=None)

    @site2_app.get("/api/coupang-trader-combo")
    def site2_coupang_trader_combo(theme: str | None = None, subId: str | None = None, category: str | None = "food"):
        return _build_coupang_combo_response(theme=theme, subId=subId, category=category)

    @site2_app.get("/api/coupang-food-ads")
    def site2_coupang_food_ads(theme: str | None = None, subId: str | None = None):
        return _build_coupang_combo_response(theme=theme, subId=subId, category="food")

    # Keep SPA/static behavior for everything else under /site2.
    site2_app.mount("/", StaticFiles(directory=SITE2_DIR, html=True), name="site2-static")
    app.mount("/site2", site2_app, name="site2")
    logger.info("site2 mount path: %s", SITE2_DIR)
else:
    logger.warning("site2 static directory not found (expected html5/site2 or site-react/dist)")
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
if INDICATOR_DIR.exists():
    # Keep the public mount name stable even if the actual folder differs.
    app.mount("/지표데이터", StaticFiles(directory=INDICATOR_DIR), name="indicator-data")


def _get_trigger_ids() -> Set[str]:
    """Return trigger IDs normalized to UPPERCASE.

    - Empty / wildcard ('*' or 'ALL') means: trigger on any webhook.
    """
    cfg = str(getattr(settings, "auto_refresh_trigger_series", "") or "").strip().upper()
    if not cfg or cfg in {"*", "ALL"}:
        return set()
    return {s.strip() for s in cfg.split(",") if s.strip()}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body_bytes = await request.body()
    body_text = body_bytes.decode(errors="ignore")
    logger.error("Validation error: %s Body: %s", exc.errors(), body_text)
    return JSONResponse(status_code=422, content={"detail": exc.errors(), "body": body_text})


@app.on_event("startup")
def startup() -> None:
    with db.db_session(settings.db_path) as conn:
        db.init_db(conn)
    logger.info("WarRoom v2.1 Engine Online. Database ready.")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "2.1"}


@app.get("/api/coupang-trader-combo")
def coupang_trader_combo(theme: str | None = None, subId: str | None = None, category: str | None = "food"):
    """3-item Coupang combo payload for inline widget."""
    return _build_coupang_combo_response(theme=theme, subId=subId, category=category)


@app.get("/api/coupang-food-ads")
def coupang_food_ads(theme: str | None = None, subId: str | None = None):
    """Food-focused Coupang ads endpoint for site2 widget."""
    return _build_coupang_combo_response(theme=theme, subId=subId, category="food")


@app.post("/order")
@app.post("/webhook/{webhook_token}")
@app.post("/webhook")
async def ingest(request: Request, background_tasks: BackgroundTasks, webhook_token: str | None = None):
    """Unified webhook ingest endpoint.

    Flow: receive -> store -> trigger daily job (BackgroundTasks) if needed.
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

    # 1) Store the raw observation.
    with db.db_session(settings.db_path) as conn:
        db.insert_observation(
            conn=conn,
            series_id=payload.series_id,
            time_utc_ms=payload.time_utc_ms,
            interval=payload.interval,
            value=payload.value,
            received_at=payload.received_at_iso,
            payload=payload.model_dump(),
        )

    # 2) Determine if this webhook should trigger a daily refresh.
    input_id = payload.series_id.upper()
    trigger_set = _get_trigger_ids()

    if not getattr(settings, "auto_refresh_daily", True):
        is_trigger = False
    elif not trigger_set:
        is_trigger = True
    else:
        # Explicit list match OR common keyword match.
        is_trigger = input_id in trigger_set or any(kw in input_id for kw in ["IXIC", "NASDAQ", "BTC", "COPPER"])

    # 3) Fire-and-forget daily job.
    if is_trigger:
        logger.info("Triggering background update for series: %s", input_id)
        background_tasks.add_task(run_daily_job)
    else:
        logger.info("Ingested %s, but not a trigger. Skipping background job.", input_id)

    return {"status": "ok", "ingested": input_id, "background_job": is_trigger}


@app.get("/")
def root() -> dict:
    return {
        "message": "WarRoom v2.1 Institutional Ingest Server",
        "uptime": datetime.now(tz=timezone.utc).isoformat(),
    }
