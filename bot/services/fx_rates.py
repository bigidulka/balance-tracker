"""FX rate provider for fiat currency conversion.

Caches rates for configurable TTL. Falls back to hardcoded rates on failure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

SUPPORTED_CURRENCIES: dict[str, dict[str, Any]] = {
    "USD": {"symbol": "$", "name_en": "US Dollar", "name_ru": "Доллар США"},
    "EUR": {"symbol": "€", "name_en": "Euro", "name_ru": "Евро"},
    "RUB": {"symbol": "₽", "name_en": "Russian Ruble", "name_ru": "Российский рубль"},
    "UAH": {"symbol": "₴", "name_en": "Ukrainian Hryvnia", "name_ru": "Украинская гривна"},
    "BYN": {"symbol": "Br", "name_en": "Belarusian Ruble", "name_ru": "Белорусский рубль"},
    "KZT": {"symbol": "₸", "name_en": "Kazakhstani Tenge", "name_ru": "Казахстанский тенге"},
    "CNY": {"symbol": "¥", "name_en": "Chinese Yuan", "name_ru": "Китайский юань"},
    "TRY": {"symbol": "₺", "name_en": "Turkish Lira", "name_ru": "Турецкая лира"},
    "GBP": {"symbol": "£", "name_en": "British Pound", "name_ru": "Британский фунт"},
}

# Fallback rates USD -> currency
_FALLBACK_RATES: dict[str, float] = {
    "USD": 1.0,
    "EUR": 0.92,
    "RUB": 92.0,
    "UAH": 41.0,
    "BYN": 3.27,
    "KZT": 460.0,
    "CNY": 7.25,
    "TRY": 34.0,
    "GBP": 0.79,
}

_cached_rates: dict[str, float] | None = None
_cached_at: float = 0.0
_cache_ttl: float = 3600.0  # 1 hour


async def fetch_fx_rates() -> dict[str, float]:
    """Fetch USD->currency rates from open exchange rate API."""
    global _cached_rates, _cached_at

    now = time.monotonic()
    if _cached_rates is not None and (now - _cached_at) < _cache_ttl:
        return _cached_rates

    url = "https://open.er-api.com/v6/latest/USD"
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json()

        rates: dict[str, float] = {"USD": 1.0}
        api_rates = data.get("rates") or {}
        for code in SUPPORTED_CURRENCIES:
            if code == "USD":
                continue
            rate = api_rates.get(code)
            if rate and float(rate) > 0:
                rates[code] = float(rate)

        if len(rates) > 1:
            _cached_rates = rates
            _cached_at = now
            logger.info("FX rates refreshed: %d currencies", len(rates))
            return rates
    except Exception as exc:
        logger.warning("FX rates fetch failed, using fallback: %s", exc)

    # Fallback
    result = dict(_FALLBACK_RATES)
    if _cached_rates is None:
        _cached_rates = result
        _cached_at = now
    return result


def convert_usd(amount_usd: float, currency: str, rates: dict[str, float]) -> float:
    """Convert USD amount to target currency."""
    rate = rates.get(currency, 1.0)
    return amount_usd * rate


def format_fiat(amount: float, currency: str) -> str:
    """Format a fiat amount with currency symbol."""
    info = SUPPORTED_CURRENCIES.get(currency, {})
    symbol = info.get("symbol", currency)
    if amount >= 1000:
        return f"{symbol}{amount:,.2f}"
    return f"{symbol}{amount:.2f}"


def currency_label(code: str, locale: str = "ru") -> str:
    info = SUPPORTED_CURRENCIES.get(code, {})
    name = info.get(f"name_{locale}") or info.get("name_en") or code
    symbol = info.get("symbol", "")
    return f"{code} ({symbol}) — {name}"


def currency_codes() -> list[str]:
    return list(SUPPORTED_CURRENCIES.keys())