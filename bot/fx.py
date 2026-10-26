"""Fixed game exchange rates for the currencies represented in the game.

These are gameplay values, not current market rates. They are deliberately
static and never fetched from an external provider.
"""
from __future__ import annotations

import math
import time
from typing import Any

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
# Rates are expressed as foreign-currency units per one RUB.
# Thus 1 USD = 66.67 RUB means 1 RUB = 1/66.67 USD.
FIXED_RATES = {
    "RUB": 1.0,
    "USD": 1 / 66.67,
    "EUR": 1 / 66.67,
    "GBP": 1 / 83.33,
    "CNY": 0.1,
    "JPY": 2.0,
    "KZT": 5.0,
    "TRY": 0.4,
}
FIXED_SOURCE = "Фиксированные игровые курсы"
# Retained only for validating legacy/provider payloads in compatibility tests;
# get_rates never uses this validation or contacts the provider.
MAX_STALE_SECONDS = 72 * 60 * 60
_cache: dict[str, Any] | None = None


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
    """Return the fixed gameplay rates; never call an external FX provider."""
    global _cache
    if _cache is None:
        _cache = {
            "rates": dict(FIXED_RATES),
            "updated_at": 0,
            "source": FIXED_SOURCE,
            "fetched_at": 0,
            "stale": False,
        }
    return dict(_cache)


def reset_cache_for_tests() -> None:
    global _cache
    _cache = None
