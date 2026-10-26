"""Live reference exchange rates for the currencies represented in the game.

The public exchangerate-api endpoint refreshes daily. These are indicative
market rates, not guaranteed executable settlement quotes.
"""
from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any

import aiohttp

from bot import db

BASE = "RUB"
CURRENCIES = [
    {"code": "RUB", "country": "RU", "name": "Российский рубль", "symbol": "₽"},
    {"code": "USD", "country": "US", "name": "Доллар США", "symbol": "$"},
    {"code": "EUR", "country": "DE", "name": "Евро", "symbol": "€"},
    {"code": "GBP", "country": "GB", "name": "Фунт стерлингов", "symbol": "£"},
    {"code": "CNY", "country": "CN", "name": "Китайский юань", "symbol": "CN¥"},
    {"code": "JPY", "country": "JP", "name": "Японская иена", "symbol": "JP¥"},
    {"code": "KZT", "country": "KZ", "name": "Казахстанский тенге", "symbol": "₸"},
    {"code": "TRY", "country": "TR", "name": "Турецкая лира", "symbol": "₺"},
]
CURRENCY_CODES = {item["code"] for item in CURRENCIES}
CURRENCY_BY_COUNTRY = {item["country"]: item["code"] for item in CURRENCIES}
CURRENCY_BY_CODE = {item["code"]: item for item in CURRENCIES}
FX_URL = os.getenv("FX_API_URL", "https://open.er-api.com/v6/latest/RUB")
CACHE_TTL_SECONDS = 6 * 60 * 60
MAX_STALE_SECONDS = 72 * 60 * 60
_timeout = aiohttp.ClientTimeout(total=3, connect=2)
_lock = asyncio.Lock()
_cache: dict[str, Any] | None = None
_failure_until = 0.0


class FXUnavailable(RuntimeError):
    pass


def currency_round(amount: float, code: str) -> float:
    """Round to the currency's minor unit (JPY has no minor denomination)."""
    if code not in CURRENCY_CODES:
        raise ValueError("unsupported_currency")
    return round(float(amount), 0 if code == "JPY" else 2)


def convert(amount: float, from_code: str, to_code: str, rates: dict[str, float]) -> float:
    """Convert using rates expressed as target-currency units per one RUB."""
    if from_code not in CURRENCY_CODES or to_code not in CURRENCY_CODES:
        raise ValueError("unsupported_currency")
    if from_code not in rates or to_code not in rates:
        raise FXUnavailable("required_rate_missing")
    result = float(amount) / float(rates[from_code]) * float(rates[to_code])
    return currency_round(result, to_code)


def rub_value(amount: float, currency: str, rates: dict[str, float]) -> float:
    if currency not in CURRENCY_CODES or currency not in rates:
        raise FXUnavailable("required_rate_missing")
    return round(float(amount) / float(rates[currency]), 2)


def _validate_rates(payload: dict) -> dict:
    if payload.get("result") != "success" or payload.get("base_code") != BASE:
        raise FXUnavailable("provider_response_invalid")
    raw = payload.get("rates")
    if not isinstance(raw, dict):
        raise FXUnavailable("provider_rates_missing")
    rates = {code: float(raw[code]) for code in CURRENCY_CODES if code in raw}
    if set(rates) != CURRENCY_CODES or any(not math.isfinite(v) or v <= 0 or v > 1e9 for v in rates.values()):
        raise FXUnavailable("required_rate_missing")
    rates[BASE] = 1.0
    updated_at = int(payload.get("time_last_update_unix") or time.time())
    now = int(time.time())
    if updated_at > now + 300 or now - updated_at > MAX_STALE_SECONDS:
        raise FXUnavailable("provider_data_stale")
    return {"rates": rates, "updated_at": updated_at, "source": "open.er-api.com"}


async def get_rates(force: bool = False) -> dict:
    """Return fresh rates, using persisted cache only when it is at most 72h old."""
    global _cache, _failure_until
    now = int(time.time())
    if _cache and not force and now - int(_cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return dict(_cache)
    async with _lock:
        now = int(time.time())
        if _cache and not force and now - int(_cache["fetched_at"]) < CACHE_TTL_SECONDS:
            return dict(_cache)
        if not force and time.monotonic() < _failure_until:
            persisted = db.fx_cache_get()
            if persisted and now - int(persisted["updated_at"]) <= MAX_STALE_SECONDS:
                snapshot = {**persisted, "fetched_at": now, "stale": True}
                _cache = snapshot
                return dict(snapshot)
            raise FXUnavailable("rate_provider_unavailable")
        try:
            async with aiohttp.ClientSession(timeout=_timeout) as session:
                async with session.get(FX_URL) as response:
                    response.raise_for_status()
                    data = await response.json()
            snapshot = _validate_rates(data)
            snapshot["fetched_at"] = now
            db.fx_cache_set(snapshot["rates"], snapshot["updated_at"], snapshot["source"])
            _cache = snapshot
            _failure_until = 0.0
            return dict(snapshot)
        except Exception as exc:
            _failure_until = time.monotonic() + 30.0
            persisted = db.fx_cache_get()
            if persisted and now - int(persisted["updated_at"]) <= MAX_STALE_SECONDS:
                snapshot = {**persisted, "fetched_at": now, "stale": True}
                _cache = snapshot
                return dict(snapshot)
            if isinstance(exc, FXUnavailable):
                raise
            raise FXUnavailable("rate_provider_unavailable") from exc


def reset_cache_for_tests() -> None:
    global _cache, _failure_until
    _cache = None
    _failure_until = 0.0
