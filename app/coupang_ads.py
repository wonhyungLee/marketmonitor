from __future__ import annotations

import csv
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TRADER_COMBO_DISCLOSURE = (
    "※ 이 글은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받을 수 있습니다."
)

logger = logging.getLogger("warroom.coupang_ads")


def _env_float(name: str, default: float, min_value: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except Exception:
        return default
    return val if val >= min_value else min_value


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except Exception:
        return default
    if val < min_value:
        return min_value
    if val > max_value:
        return max_value
    return val


_COUPANG_API_MIN_INTERVAL_SEC = _env_float("COUPANG_API_MIN_INTERVAL_SEC", default=1.2, min_value=0.3)
_COUPANG_API_MAX_RETRIES = _env_int("COUPANG_API_MAX_RETRIES", default=4, min_value=1, max_value=8)
_COUPANG_API_RETRY_MAX_SLEEP_SEC = _env_float("COUPANG_API_RETRY_MAX_SLEEP_SEC", default=20.0, min_value=1.0)
_COUPANG_NEGATIVE_CACHE_TTL_SEC = _env_int("COUPANG_NEGATIVE_CACHE_TTL_SEC", default=900, min_value=60, max_value=86400)

_COUPANG_RATE_LOCK = threading.Lock()
_COUPANG_LAST_CALL_MONOTONIC = 0.0


def _encode_component(value: Any) -> str:
    # JS encodeURIComponent-compatible.
    return urllib.parse.quote(str(value or ""), safe="-_.!~*'()")


def _signed_date(now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.strftime("%y%m%dT%H%M%SZ")


def _hmac_sha256_hex(secret_key: str, message: str) -> str:
    return hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _load_keys_from_api_info_file() -> None:
    """Best-effort loader (keeps secrets out of git).

    Set either environment variables:
      - COUPANG_ACCESS_KEY
      - COUPANG_SECRET_KEY

    Or point COUPANG_API_INFO_FILE to your local txt file.
    """

    if (os.getenv("COUPANG_ACCESS_KEY") or "").strip() and (os.getenv("COUPANG_SECRET_KEY") or "").strip():
        return

    def _parse_api_info(text: str) -> Tuple[Optional[str], Optional[str]]:
        access_key: Optional[str] = None
        secret_key: Optional[str] = None
        lines = [line.strip() for line in (text or "").splitlines()]

        for idx, line in enumerate(lines):
            low = line.lower()

            # Supports both:
            # - "Access key: xxxx"
            # - "Access key" (next line contains value)
            if "access key" in low or "access_key" in low:
                if ":" in line:
                    maybe = line.split(":", 1)[1].strip()
                    if maybe:
                        access_key = maybe
                if not access_key:
                    for nxt in lines[idx + 1 :]:
                        if nxt:
                            access_key = nxt
                            break

            if "secret key" in low or "secret_key" in low:
                if ":" in line:
                    maybe = line.split(":", 1)[1].strip()
                    if maybe:
                        secret_key = maybe
                if not secret_key:
                    for nxt in lines[idx + 1 :]:
                        if nxt:
                            secret_key = nxt
                            break

            if "=" in line:
                k, v = line.split("=", 1)
                key = k.strip().lower()
                val = v.strip()
                if key in {"access_key", "accesskey", "coupang_access_key"} and val:
                    access_key = val
                if key in {"secret_key", "secretkey", "coupang_secret_key"} and val:
                    secret_key = val

        # Fallback for uuid-like key lines.
        uuid_like = [x for x in lines if re.fullmatch(r"[0-9a-fA-F-]{30,}", x or "")]
        if not access_key and len(uuid_like) >= 1:
            access_key = uuid_like[0]
        if not secret_key and len(uuid_like) >= 2:
            secret_key = uuid_like[1]

        return access_key, secret_key

    candidates: List[Path] = []
    env_path = (os.getenv("COUPANG_API_INFO_FILE") or "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    candidates.extend(
        [
            Path("쿠팡파트너스api정보.txt"),
            Path("../쿠팡파트너스api정보.txt"),
            Path("/home/ubuntu/쿠팡파트너스api정보.txt"),
            Path("/home/ubuntu/쿠팡광고2026/쿠팡파트너스api정보.txt"),
            Path("/opt/marketmonitor/쿠팡파트너스api정보.txt"),
        ]
    )

    for path in candidates:
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        ak, sk = _parse_api_info(text)
        if ak and sk:
            os.environ.setdefault("COUPANG_ACCESS_KEY", ak)
            os.environ.setdefault("COUPANG_SECRET_KEY", sk)
            return


def _get_credentials() -> Tuple[str, str]:
    _load_keys_from_api_info_file()
    access_key = (os.getenv("COUPANG_ACCESS_KEY") or "").strip()
    secret_key = (os.getenv("COUPANG_SECRET_KEY") or "").strip()
    if not access_key or not secret_key:
        raise RuntimeError("Missing COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY")
    return access_key, secret_key


def _wait_coupang_slot() -> None:
    global _COUPANG_LAST_CALL_MONOTONIC

    interval = max(_COUPANG_API_MIN_INTERVAL_SEC, 0.0)
    if interval <= 0:
        return

    with _COUPANG_RATE_LOCK:
        now_mono = time.monotonic()
        wait_sec = interval - (now_mono - _COUPANG_LAST_CALL_MONOTONIC)
        if wait_sec > 0:
            time.sleep(wait_sec)
        _COUPANG_LAST_CALL_MONOTONIC = time.monotonic()


def _parse_retry_after_seconds(raw_value: str | None) -> Optional[float]:
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except Exception:
        pass
    try:
        retry_dt = parsedate_to_datetime(value)
        if retry_dt.tzinfo is None:
            retry_dt = retry_dt.replace(tzinfo=dt.timezone.utc)
        delta = (retry_dt - dt.datetime.now(dt.timezone.utc)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


def _retry_wait_seconds(attempt_idx: int, retry_after: Optional[float] = None) -> float:
    base = max(_COUPANG_API_MIN_INTERVAL_SEC, 0.5)
    if retry_after is not None:
        return min(max(retry_after, base), _COUPANG_API_RETRY_MAX_SLEEP_SEC)
    return min(base * (2 ** attempt_idx), _COUPANG_API_RETRY_MAX_SLEEP_SEC)


def _looks_like_rate_limited(message: str) -> bool:
    low = (message or "").lower()
    return (
        "rate limit" in low
        or "too many request" in low
        or "too_many_request" in low
        or "429" in low
    )


def _fetch_coupang_search_products(keyword: str, limit: int, sub_id: str) -> List[Dict[str, Any]]:
    access_key, secret_key = _get_credentials()
    path = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"

    # Keep outbound search volume minimal to avoid Coupang API throttling.
    try:
        safe_limit = int(limit)
    except Exception:
        safe_limit = 1
    safe_limit = max(1, min(safe_limit, 1))

    query = (
        f"keyword={_encode_component(keyword)}"
        f"&limit={_encode_component(str(safe_limit))}"
        f"&subId={_encode_component(sub_id)}"
    )
    url = f"https://api-gateway.coupang.com{path}?{query}"

    last_error: Optional[Exception] = None
    for attempt in range(_COUPANG_API_MAX_RETRIES):
        _wait_coupang_slot()
        signed_date = _signed_date()
        message = f"{signed_date}GET{path}{query}"
        signature = _hmac_sha256_hex(secret_key, message)
        authorization = (
            "CEA algorithm=HmacSHA256, "
            f"access-key={access_key}, "
            f"signed-date={signed_date}, "
            f"signature={signature}"
        )

        req = urllib.request.Request(url, headers={"Authorization": authorization})
        try:
            with urllib.request.urlopen(req, timeout=8) as res:
                body = res.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            body = exc.read().decode("utf-8", errors="ignore")
            retry_after = _parse_retry_after_seconds((exc.headers or {}).get("Retry-After"))

            if status in {429, 500, 502, 503, 504} and attempt + 1 < _COUPANG_API_MAX_RETRIES:
                wait_sec = _retry_wait_seconds(attempt, retry_after=retry_after)
                logger.warning(
                    "coupang api retry http=%s attempt=%s/%s wait=%.2fs keyword=%s",
                    status,
                    attempt + 1,
                    _COUPANG_API_MAX_RETRIES,
                    wait_sec,
                    keyword,
                )
                time.sleep(wait_sec)
                continue

            msg = body.strip()[:220] or f"HTTP {status}"
            raise RuntimeError(f"Coupang API HTTP {status}: {msg}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt + 1 < _COUPANG_API_MAX_RETRIES:
                wait_sec = _retry_wait_seconds(attempt)
                logger.warning(
                    "coupang api network retry attempt=%s/%s wait=%.2fs keyword=%s err=%s",
                    attempt + 1,
                    _COUPANG_API_MAX_RETRIES,
                    wait_sec,
                    keyword,
                    exc,
                )
                time.sleep(wait_sec)
                continue
            raise RuntimeError(f"Coupang API network error: {exc}") from exc

        data: Dict[str, Any] = {}
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if isinstance(data, dict) and str(data.get("rCode", "0")) != "0":
            msg = str(data.get("rMessage") or "Coupang API error")
            if _looks_like_rate_limited(msg) and attempt + 1 < _COUPANG_API_MAX_RETRIES:
                wait_sec = _retry_wait_seconds(attempt)
                logger.warning(
                    "coupang api logical retry attempt=%s/%s wait=%.2fs keyword=%s msg=%s",
                    attempt + 1,
                    _COUPANG_API_MAX_RETRIES,
                    wait_sec,
                    keyword,
                    msg,
                )
                time.sleep(wait_sec)
                continue
            raise RuntimeError(msg)

        products = (data.get("data") or {}).get("productData") if isinstance(data, dict) else None
        return list(products) if isinstance(products, list) else []

    if last_error is not None:
        raise RuntimeError(f"Coupang API request failed: {last_error}") from last_error
    raise RuntimeError("Coupang API request failed after retries")


def _parse_category_code(category: str) -> str:
    s = (category or "").strip()
    return s[:1].upper() if s else ""


def _fmt_krw(value: int) -> str:
    try:
        return f"{int(value):,}원"
    except Exception:
        return ""


# Template loading

_TEMPLATE_LOCK = threading.Lock()
_TEMPLATE_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "items": []}


def _candidate_food_template_files() -> List[Path]:
    override = (
        os.getenv("COUPANG_FOOD_TEMPLATE_PATH")
        or os.getenv("COUPANG_FOOD_TEMPLATE_FILE")
        or os.getenv("COUPANG_FOOD_TEMPLATE")
        or ""
    ).strip()

    here = Path(__file__).resolve()
    project_root = here.parents[1]  # .../app -> repo root

    candidates: List[Path] = []
    if override:
        candidates.append(Path(override).expanduser())

    candidates.extend(
        [
            project_root / "data" / "coupang_food_template.csv",
            project_root / "data" / "coupang_links_template.csv",
            project_root / "coupang_food_template.csv",
            project_root / "coupang_links_template.csv",
            Path("/opt/marketmonitor/data/coupang_food_template.csv"),
            Path("/home/ubuntu/data/coupang_food_template.csv"),
        ]
    )

    uniq: List[Path] = []
    seen = set()
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        uniq.append(p)
        seen.add(key)
    return uniq


def _load_food_template_items() -> List[Dict[str, Any]]:
    path: Optional[Path] = None
    for p in _candidate_food_template_files():
        try:
            if p.exists() and p.is_file():
                path = p
                break
        except Exception:
            continue
    if path is None:
        return []

    try:
        mtime = path.stat().st_mtime
    except Exception:
        mtime = None

    with _TEMPLATE_LOCK:
        if (
            _TEMPLATE_CACHE.get("path") == str(path)
            and _TEMPLATE_CACHE.get("mtime") == mtime
            and _TEMPLATE_CACHE.get("items")
        ):
            return list(_TEMPLATE_CACHE["items"])

        items: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    idx = int(str(row.get("idx", "")).strip() or "0")
                except Exception:
                    idx = 0
                category = str(row.get("category", "") or "").strip()
                food_name = str(row.get("food_name", "") or "").strip()
                product_name = str(row.get("product_name", "") or "").strip()
                affiliate_link = str(row.get("affiliate_link", "") or "").strip()
                if not (idx and (food_name or product_name)):
                    continue
                items.append(
                    {
                        "idx": idx,
                        "category": category,
                        "category_code": _parse_category_code(category),
                        "food_name": food_name,
                        "product_name": product_name,
                        "affiliate_link": affiliate_link,
                    }
                )

        _TEMPLATE_CACHE["path"] = str(path)
        _TEMPLATE_CACHE["mtime"] = mtime
        _TEMPLATE_CACHE["items"] = items
        return list(items)


TRADER_THEMES: Dict[str, Dict[str, Any]] = {
    "focus": {
        "title": "트레이더 집중력 3종 세트",
        "tagline": "커피/차 + 고단백 + 바삭 간식",
        "cta": "지금 쿠팡에서 확인",
        "recipes": [[3, 22, 40], [1, 23, 35], [4, 21, 33]],
    },
    "fresh": {
        "title": "트레이더 상큼 3종 세트",
        "tagline": "무가당 음료 + 견과 + 과일",
        "cta": "상큼하게 리셋",
        "recipes": [[11, 16, 45], [6, 18, 42], [9, 17, 47]],
    },
    "meal": {
        "title": "장중 든든 3종 세트",
        "tagline": "빠른 한 끼 + 음료 + 단백질",
        "cta": "장중 에너지 충전",
        "recipes": [[52, 11, 29], [56, 12, 16], [63, 14, 48]],
    },
    "night": {
        "title": "야간/마감 후 3종 세트",
        "tagline": "야식 + 제로 음료 + 단짠",
        "cta": "마감 후 한 입",
        "recipes": [[65, 12, 33], [66, 11, 35], [69, 12, 34]],
    },
}


def _auto_theme_id(now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    kst = now + dt.timedelta(hours=9)
    hour = kst.hour
    if hour >= 22 or hour < 6:
        return "night"
    if 6 <= hour < 11:
        return "focus"
    if 11 <= hour < 17:
        return "meal"
    return "fresh"


_PRODUCT_CACHE_LOCK = threading.Lock()
_PRODUCT_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_PRODUCT_NEGATIVE_CACHE: Dict[Tuple[str, str], float] = {}
_PRODUCT_FETCH_LOCK = threading.Lock()


def _get_product_cached(keyword: str, sub_id: str, ttl_sec: int = 6 * 3600) -> Optional[Dict[str, Any]]:
    cache_key = (keyword.strip(), (sub_id or "").strip())
    now_ts = dt.datetime.now(dt.timezone.utc).timestamp()
    with _PRODUCT_CACHE_LOCK:
        hit = _PRODUCT_CACHE.get(cache_key)
        if hit:
            ts, data = hit
            if now_ts - ts < ttl_sec:
                return dict(data)
        blocked_until = _PRODUCT_NEGATIVE_CACHE.get(cache_key)
        if blocked_until and now_ts < blocked_until:
            return None
        if blocked_until and now_ts >= blocked_until:
            _PRODUCT_NEGATIVE_CACHE.pop(cache_key, None)

    # Serialize live OpenAPI calls to prevent burst traffic from concurrent requests.
    with _PRODUCT_FETCH_LOCK:
        now_ts = dt.datetime.now(dt.timezone.utc).timestamp()
        with _PRODUCT_CACHE_LOCK:
            hit = _PRODUCT_CACHE.get(cache_key)
            if hit:
                ts, data = hit
                if now_ts - ts < ttl_sec:
                    return dict(data)
            blocked_until = _PRODUCT_NEGATIVE_CACHE.get(cache_key)
            if blocked_until and now_ts < blocked_until:
                return None
            if blocked_until and now_ts >= blocked_until:
                _PRODUCT_NEGATIVE_CACHE.pop(cache_key, None)

        try:
            products = _fetch_coupang_search_products(keyword=keyword, limit=1, sub_id=sub_id)
        except Exception as exc:
            with _PRODUCT_CACHE_LOCK:
                _PRODUCT_NEGATIVE_CACHE[cache_key] = now_ts + _COUPANG_NEGATIVE_CACHE_TTL_SEC
            logger.warning("coupang product lookup failed keyword=%s err=%s", keyword, exc)
            return None

        if not products:
            with _PRODUCT_CACHE_LOCK:
                _PRODUCT_NEGATIVE_CACHE[cache_key] = now_ts + _COUPANG_NEGATIVE_CACHE_TTL_SEC
            return None

        p0 = products[0] if isinstance(products[0], dict) else {}
        title = str(p0.get("productName") or "").strip()
        image = str(p0.get("productImage") or "").strip()
        link = str(p0.get("productUrl") or "").strip()
        try:
            price = int(p0.get("productPrice") or 0)
        except Exception:
            price = 0

        payload = {
            "productName": title,
            "productImage": image,
            "productUrl": link,
            "productPrice": price,
        }
        with _PRODUCT_CACHE_LOCK:
            _PRODUCT_CACHE[cache_key] = (now_ts, payload)
            _PRODUCT_NEGATIVE_CACHE.pop(cache_key, None)
        return dict(payload)


_COMBO_CACHE_LOCK = threading.Lock()
_COMBO_CACHE: Dict[Tuple[str, str, int], Tuple[float, Dict[str, Any]]] = {}
_COMBO_BUILD_LOCK = threading.Lock()


def build_trader_combo_payload(
    theme: str | None = None,
    sub_id: str | None = None,
    category: str | None = "food",
) -> Dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    category_key = (category or "food").strip().lower()
    if category_key not in {"food", "foods", "식품"}:
        category_key = "food"

    theme_id = (theme or "").strip().lower()
    if not theme_id or theme_id == "auto":
        theme_id = _auto_theme_id(now)
    theme_conf = TRADER_THEMES.get(theme_id) or TRADER_THEMES["focus"]
    theme_id = next((k for k, v in TRADER_THEMES.items() if v is theme_conf), theme_id)

    sub_id = (sub_id or "").strip() or (os.getenv("COUPANG_SUB_ID") or "").strip() or "marketmonitor"

    slot = int(now.timestamp() // (30 * 60))
    cache_key = (theme_id, sub_id, slot)
    with _COMBO_CACHE_LOCK:
        hit = _COMBO_CACHE.get(cache_key)
        if hit:
            ts, payload = hit
            if now.timestamp() - ts < 25 * 60:
                return dict(payload)

    with _COMBO_BUILD_LOCK:
        now = dt.datetime.now(dt.timezone.utc)
        slot = int(now.timestamp() // (30 * 60))
        cache_key = (theme_id, sub_id, slot)
        with _COMBO_CACHE_LOCK:
            hit = _COMBO_CACHE.get(cache_key)
            if hit:
                ts, payload = hit
                if now.timestamp() - ts < 25 * 60:
                    return dict(payload)

        template_items = _load_food_template_items()
        if category_key == "food":
            template_items = [it for it in template_items if str(it.get("category", "")).strip()]

        if not template_items:
            return {
                "ok": False,
                "message": "coupang food template not found",
                "theme": {"id": theme_id, "title": theme_conf.get("title"), "tagline": theme_conf.get("tagline")},
                "items": [],
            }

        by_idx = {int(it["idx"]): it for it in template_items if it.get("idx")}
        recipes = list(theme_conf.get("recipes") or [])
        recipe = recipes[slot % len(recipes)] if recipes else [1, 22, 33]
        picked = [by_idx[i] for i in recipe if i in by_idx]

        items: List[Dict[str, Any]] = []
        total = 0
        for row in picked[:3]:
            keyword = (row.get("product_name") or row.get("food_name") or "").strip()
            if not keyword:
                continue
            prod = _get_product_cached(keyword, sub_id)
            if not prod:
                link = (row.get("affiliate_link") or "").strip()
                if not link:
                    continue
                items.append(
                    {
                        "idx": row.get("idx"),
                        "category": row.get("category"),
                        "category_code": row.get("category_code"),
                        "food_name": row.get("food_name"),
                        "keyword": keyword,
                        "title": keyword,
                        "image": "",
                        "link": link,
                        "price_krw": 0,
                        "price": "쿠팡에서 확인",
                        "source": "template_fallback",
                    }
                )
                continue

            price = int(prod.get("productPrice") or 0)
            total += max(price, 0)
            items.append(
                {
                    "idx": row.get("idx"),
                    "category": row.get("category"),
                    "category_code": row.get("category_code"),
                    "food_name": row.get("food_name"),
                    "keyword": keyword,
                    "title": prod.get("productName") or keyword,
                    "image": prod.get("productImage") or "",
                    "link": prod.get("productUrl") or row.get("affiliate_link") or "",
                    "price_krw": price,
                    "price": _fmt_krw(price) if price > 0 else "쿠팡에서 확인",
                    "source": "openapi_search",
                }
            )

        payload: Dict[str, Any] = {
            "ok": True,
            "theme": {
                "id": theme_id,
                "title": theme_conf.get("title") or "트레이더 추천 3종 세트",
                "tagline": theme_conf.get("tagline") or "",
                "cta": theme_conf.get("cta") or "쿠팡에서 보기",
            },
            "category": "food",
            "items": items,
            "total_price_krw": int(total),
            "total_price": _fmt_krw(int(total)) if total > 0 else "",
            "disclosure": TRADER_COMBO_DISCLOSURE,
            "generated_at": now.isoformat(),
            "sub_id": sub_id,
        }
        with _COMBO_CACHE_LOCK:
            _COMBO_CACHE[cache_key] = (now.timestamp(), payload)
        return dict(payload)
