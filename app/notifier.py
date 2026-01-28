import logging
import time
from typing import Dict, Optional, Tuple

import requests

from app.engine import EngineResult
from app.settings import get_settings

# Discord Embed colors (hex as int)
COLORS = {
    "WARMUP": 0x3498DB,  # Blue
    "NORMAL": 0x2ECC71,  # Green
    "DEFCON2": 0xE67E22,  # Orange
    "DEFCON1": 0xE74C3C,  # Red
}

EMOJI = {
    "WARMUP": "⚙️",
    "NORMAL": "✅",
    "DEFCON2": "⚠️",
    "DEFCON1": "🚨",
}

logger = logging.getLogger("warroom.notifier")


def _truncate(text: str, limit: int = 1800) -> str:
    """Discord content is limited (generally 2000 chars). Keep a safety margin."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 15] + "…(truncated)"


def _safe_preview(text: str, limit: int = 500) -> str:
    if text is None:
        return ""
    return text if len(text) <= limit else (text[:limit] + "…")


def _parse_retry_after(headers: dict) -> Optional[float]:
    ra = headers.get("Retry-After") or headers.get("retry-after")
    if not ra:
        return None
    try:
        return float(ra)
    except Exception:
        return None


def _post_with_retry(
    client: requests.Session,
    url: str,
    payload: dict,
    timeout_sec: int,
    retry_max: int,
) -> Tuple[bool, Optional[int], str]:
    """Send a Discord webhook with basic retry + rate-limit handling.

    Returns (ok, status_code, message).
    """
    backoff = 1.0
    last_status: Optional[int] = None
    last_msg = ""

    for attempt in range(1, max(1, retry_max) + 1):
        try:
            resp = client.post(url, json=payload, timeout=timeout_sec)
            last_status = resp.status_code
            last_msg = _safe_preview(resp.text)

            # Success
            if resp.status_code in (200, 204):
                return True, resp.status_code, "ok"

            # Rate limited
            if resp.status_code == 429:
                wait = _parse_retry_after(resp.headers) or backoff
                logger.warning(
                    "discord rate-limited (429). attempt=%s/%s wait=%.1fs body=%s",
                    attempt,
                    retry_max,
                    wait,
                    _safe_preview(resp.text),
                )
                if attempt >= retry_max:
                    return False, resp.status_code, "rate-limited"
                time.sleep(min(wait, 60.0))
                backoff = min(backoff * 2.0, 30.0)
                continue

            # Temporary server errors
            if 500 <= resp.status_code < 600:
                logger.warning(
                    "discord server error. status=%s attempt=%s/%s wait=%.1fs body=%s",
                    resp.status_code,
                    attempt,
                    retry_max,
                    backoff,
                    _safe_preview(resp.text),
                )
                if attempt >= retry_max:
                    return False, resp.status_code, "server-error"
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
                continue

            # Payload issues (often 400). Try a simplified payload once.
            if resp.status_code == 400 and payload.get("embeds"):
                logger.warning(
                    "discord rejected payload (400). Retrying with simplified content-only payload. body=%s",
                    _safe_preview(resp.text),
                )
                simplified = {"content": payload.get("content", "[WarRoom] update")}
                payload = simplified
                # don't consume a retry with extra sleep here
                continue

            # Other 4xx are usually permanent (bad URL, perms, etc.)
            logger.error(
                "discord webhook failed. status=%s body=%s",
                resp.status_code,
                _safe_preview(resp.text),
            )
            return False, resp.status_code, "client-error"

        except requests.exceptions.RequestException as exc:
            last_msg = str(exc)
            logger.warning(
                "discord request error. attempt=%s/%s wait=%.1fs error=%s",
                attempt,
                retry_max,
                backoff,
                exc,
                exc_info=True,
            )
            if attempt >= retry_max:
                return False, last_status, "request-exception"
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)

    return False, last_status, last_msg or "unknown"

def build_embed(result: EngineResult) -> Dict:
    color = COLORS.get(result.state, COLORS["NORMAL"])
    emoji = EMOJI.get(result.state, "")
    title = f"{emoji} Market State — {result.state}"

    trend = result.reasons.get("trend") or {}
    trend_sig = str(trend.get("signal") or "UNKNOWN").upper()
    trend_window = trend.get("window")
    trend_price = trend.get("price")
    trend_ma = trend.get("ma")
    trend_vol_window = trend.get("vol_window")
    trend_vol_ann = trend.get("vol_ann")
    if isinstance(trend_window, (int, float)):
        trend_window = int(trend_window)
    else:
        trend_window = None

    trend_parts = [trend_sig]
    if trend_window and trend_price is not None and trend_ma is not None:
        trend_parts.append(f"P={trend_price:.2f} / MA{trend_window}={trend_ma:.2f}")
    elif trend_price is not None:
        trend_parts.append(f"P={trend_price:.2f}")
    if isinstance(trend_vol_window, (int, float)) and isinstance(trend_vol_ann, (int, float)):
        trend_parts.append(f"VOL{int(trend_vol_window)}={float(trend_vol_ann) * 100.0:.1f}%")

    alloc = result.reasons.get("allocation") or {}
    action = alloc.get("action")
    equity_weight = alloc.get("equity_weight")
    equity_pct = alloc.get("equity_weight_pct")

    fields = [
        {"name": "Score", "value": str(result.score) if result.score is not None else "N/A", "inline": True},
        {"name": "As of", "value": result.as_of_date, "inline": True},
        {"name": "Trend", "value": " — ".join(trend_parts), "inline": True},
        {
            "name": "Equity Weight",
            "value": f"{equity_pct:.1f}%" if isinstance(equity_pct, (int, float)) else (f"{equity_weight:.2f}" if isinstance(equity_weight, (int, float)) else "N/A"),
            "inline": True,
        },
    ]

    if action:
        fields.append({"name": "Action", "value": str(action), "inline": False})

    portfolio = result.reasons.get("portfolio") or {}
    weights = portfolio.get("weights") or {}
    if isinstance(weights, dict) and weights:
        try:
            items = [(str(k), float(v)) for k, v in weights.items() if v is not None]
            items.sort(key=lambda kv: kv[1], reverse=True)
            top = items[:6]
            lines = [f"{k}: {v * 100.0:.1f}%" for k, v in top if v > 0]

            cash_w = portfolio.get("cash_weight")
            gross = portfolio.get("gross_exposure")
            meta = []
            if isinstance(gross, (int, float)):
                meta.append(f"Gross {float(gross):.2f}x")
            if isinstance(cash_w, (int, float)):
                meta.append(f"Cash {float(cash_w) * 100.0:.1f}%")
            if meta:
                lines.append(" / ".join(meta))

            p_date = portfolio.get("portfolio_date") or portfolio.get("as_of_date") or result.as_of_date
            value = f"{p_date}\n" + "\n".join(lines)
            fields.append({"name": "Portfolio", "value": value, "inline": False})
        except Exception:
            pass

    duration_sec = result.reasons.get("duration_sec")
    if duration_sec is not None:
        fields.append({"name": "Compute Time", "value": f"{duration_sec:.1f}s", "inline": True})

    triggers = result.reasons.get("triggers", [])
    if triggers:
        fields.append({"name": "Triggers", "value": "\n".join(triggers), "inline": False})

    components = result.reasons.get("components", {})
    if components:
        comp_lines = [f"{k}: {v:+.2f}" for k, v in components.items()]
        fields.append({"name": "Components", "value": "\n".join(comp_lines), "inline": False})

    health_lines = []
    for sid, info in result.health.items():
        stale_flag = "stale" if info.get("stale") else "fresh"
        health_lines.append(f"{sid}: {info.get('have')}/{info.get('need')} ({stale_flag})")
    if health_lines:
        fields.append({"name": "Health", "value": "\n".join(health_lines), "inline": False})

    embed = {
        "title": title,
        "color": color,
        "fields": fields,
    }
    return embed


def notify(result: EngineResult, session: Optional[requests.Session] = None) -> bool:
    settings = get_settings()
    if not getattr(settings, "discord_enabled", True):
        logger.info("Discord notifications disabled (DISCORD_ENABLED=false)")
        return False

    url = (settings.discord_webhook_url or "").strip()
    if not url:
        logger.warning("DISCORD_WEBHOOK_URL not set; skipping notification")
        return False
    if not (url.startswith("http://") or url.startswith("https://")):
        logger.error("DISCORD_WEBHOOK_URL looks invalid (missing http/https). skipping.")
        return False

    content = _truncate(f"[WarRoom] {result.as_of_date} | {result.state} | score={result.score}")
    payload = {"content": content, "embeds": [build_embed(result)]}
    client = session or requests.Session()
    ok, status, msg = _post_with_retry(
        client=client,
        url=url,
        payload=payload,
        timeout_sec=getattr(settings, "discord_timeout_sec", 8),
        retry_max=getattr(settings, "discord_retry_max", 3),
    )

    if ok:
        logger.info("discord webhook sent")
        return True

    logger.error("discord webhook not sent. status=%s reason=%s", status, msg)
    return False
