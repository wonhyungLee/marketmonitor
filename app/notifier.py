import logging
from typing import Dict, Optional

import requests

from app.engine import EngineResult
from app.settings import get_settings


COLORS = {
    "WARMUP": 0x3498DB,
    "NORMAL": 0x2ECC71,
    "DEFCON2": 0xE67E22,
    "DEFCON1": 0xE74C3C,
}

EMOJI = {
    "WARMUP": "🟦",
    "NORMAL": "🟢",
    "DEFCON2": "🟠",
    "DEFCON1": "🔴",
}

logger = logging.getLogger("warroom.notifier")


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


def notify(result: EngineResult, session: Optional[requests.Session] = None) -> None:
    settings = get_settings()
    if not settings.discord_webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set; skipping notification")
        return

    payload = {"embeds": [build_embed(result)]}
    client = session or requests.Session()
    resp = client.post(settings.discord_webhook_url, json=payload, timeout=10)
    if resp.status_code >= 300:
        logger.error("failed to send discord webhook: %s %s", resp.status_code, resp.text)
    else:
        logger.info("discord webhook sent")
