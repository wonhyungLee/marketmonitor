import json
import re
from datetime import datetime, timezone
from typing import Optional, Union

from pydantic import BaseModel, field_validator, model_validator

SERIES_ID_ALIASES = {
    "IXIC": "NASDAQ_DLY_IXIC",
    "NASDAQ": "NASDAQ_DLY_IXIC",
    "NASDAQ_IXIC": "NASDAQ_DLY_IXIC",
    "US100": "NASDAQ_DLY_IXIC",
    "CAPITALCOM_US100": "NASDAQ_DLY_IXIC",
    "FXCM_COPPER": "COPPER",
}


def _to_ms(value: Union[int, float, str]) -> int:
    if isinstance(value, (int, float)):
        val = int(value)
        return val if val > 10_000_000_000 else val * 1000
    if isinstance(value, str):
        stripped = value.strip()
        # numeric string
        if stripped.replace(".", "", 1).isdigit():
            val = float(stripped)
            val_int = int(val)
            return val_int if val_int > 10_000_000_000 else val_int * 1000
        # Accept ISO8601 like "2026-01-23T01:50:01Z"
        iso = stripped.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return int(dt.timestamp() * 1000)
    raise ValueError("unsupported timestamp type")


def _load_json_loose(text: str) -> Optional[dict]:
    """Try strict json, then fix unquoted ISO timestamps (e.g., time_utc:2026-01-23T02:28:01Z)."""
    def _try_parse(raw: str) -> Optional[dict]:
        try:
            return json.loads(raw)
        except Exception:
            return None

    parsed = _try_parse(text)
    if parsed is not None:
        return parsed

    # If the body contains extra text, try to extract the first JSON object.
    start = text.find("{")
    end = text.rfind("}")
    candidate = text
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        parsed = _try_parse(candidate)
        if parsed is not None:
            return parsed

    fixed = re.sub(
        r'"(time_utc|timenow|time_now|time\s+now|timeUTC|time\s+utc|timestamp|time)"\s*:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}T[^",}]+)',
        r'"\1":"\2"',
        candidate,
    )
    return _try_parse(fixed)


class TradingViewPayload(BaseModel):
    schema_version: int = 1
    source: str = "tradingview"
    series_id: str
    time_utc: Union[int, float, str]
    interval: str
    value: Union[int, float, str]
    timenow: Optional[Union[int, float, str]] = None

    model_config = {"extra": "allow", "populate_by_name": True}

    @model_validator(mode="before")
    def normalize_aliases(cls, values):
        # Accept raw bytes or JSON string bodies
        if isinstance(values, (bytes, bytearray)):
            decoded = values.decode("utf-8", errors="ignore")
            parsed = _load_json_loose(decoded)
            values = parsed if parsed is not None else {"raw_body": decoded}
        elif isinstance(values, str):
            parsed = _load_json_loose(values)
            values = parsed if parsed is not None else {"raw_body": values}
        # map alternative keys from TradingView templates
        if "time_utc" not in values:
            for key in ("time", "timeUTC", "time utc", "timestamp", "t", "bar_time"):
                if key in values:
                    values["time_utc"] = values[key]
                    break
        if "timenow" not in values:
            for key in ("time_now", "time now", "timenow"):
                if key in values:
                    values["timenow"] = values[key]
                    break
        if "series_id" not in values and "seriesId" in values:
            values["series_id"] = values["seriesId"]
        if "series_id" not in values:
            for key in ("ticker", "symbol", "ticker_id", "instrument", "series", "market", "security"):
                if key in values and values[key]:
                    values["series_id"] = values[key]
                    break
        if "schema_version" not in values and "schemaVersion" in values:
            values["schema_version"] = values["schemaVersion"]
        if "interval" not in values:
            for key in ("resolution", "timeframe", "tf", "period"):
                if key in values:
                    values["interval"] = values[key]
                    break
        if "value" not in values:
            for key in ("close", "price", "last", "last_price", "c"):
                if key in values:
                    values["value"] = values[key]
                    break
        return values

    @field_validator("source")
    def validate_source(cls, v: str) -> str:
        if v.lower() != "tradingview":
            raise ValueError("source must be tradingview")
        return v

    @field_validator("schema_version")
    def validate_schema_version(cls, v: Union[int, str]) -> int:
        iv = int(v)
        if iv != 1:
            raise ValueError("unsupported schema_version")
        return iv

    @field_validator("interval")
    def normalize_interval(cls, v: str) -> str:
        return v.upper()

    @field_validator("series_id")
    def normalize_series_id(cls, v: str) -> str:
        if not isinstance(v, str):
            return v
        raw = v.strip().upper() # Always work with uppercase
        if not raw:
            return v
        alias_key = re.sub(r"[^A-Z0-9]+", "_", raw).strip("_")
        # Map aliases (e.g., IXIC -> NASDAQ_DLY_IXIC)
        mapped = SERIES_ID_ALIASES.get(alias_key) or SERIES_ID_ALIASES.get(raw)
        if mapped:
            return mapped
        # Default to normalized alias_key (e.g., NASDAQ:DLY:IXIC -> NASDAQ_DLY_IXIC)
        return alias_key or raw # Returns canonical name in uppercase

    @field_validator("value")
    def normalize_value(cls, v: Union[int, float, str]) -> float:
        return float(v)

    @property
    def time_utc_ms(self) -> int:
        return _to_ms(self.time_utc)

    @property
    def received_at_iso(self) -> str:
        ts_val = self.timenow if self.timenow is not None else int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        ts_ms = _to_ms(ts_val)
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return dt.isoformat()
