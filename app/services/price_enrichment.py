"""
3-tier token price enrichment by contract address + native coin price helper.

Tier 1: DexScreener  — 300 req/min, aiohttp, batch up to 30
Tier 2: GeckoTerminal — 30 req/min, aiohttp, batch up to 30
Tier 3: DEXTools      — aiohttp (CF-mimic headers), per-address, last resort

Usage:
    from app.services.price_enrichment import enrich_prices, get_native_price, TokenPrice

    # Contract-based tokens
    tokens = [
        TokenPrice(address="0xabc...", chain="ethereum"),
        TokenPrice(address="0xdef...", chain="bsc"),
    ]
    await enrich_prices(tokens, session)

    # Native chain coins (TRX, TON, SOL, SUI, etc.) — resolved via Binance
    trx_price = await get_native_price("TRX", session)
    ton_price = await get_native_price("TON", session)

Chain names accepted: lowercase common names ("ethereum", "bsc", "solana",
"arbitrum", "polygon", "base", "avalanche", "sui", "tron", etc.).
See _DS_CHAIN_SLUGS / _GT_CHAIN_SLUGS for full mapping.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chain slug maps
# ---------------------------------------------------------------------------

# DexScreener network identifiers
_DS_CHAIN_SLUGS: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "bsc": "bsc",
    "binance": "bsc",
    "binance-smart-chain": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arbitrum-one": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "cronos": "cronos",
    "solana": "solana",
    "sol": "solana",
    "sui": "sui",
    "tron": "tron",
    "trx": "tron",
    "ton": "ton",
    "aptos": "aptos",
    "near": "near",
    "linea": "linea",
    "scroll": "scroll",
    "zksync": "zksync",
    "blast": "blast",
    "mantle": "mantle",
    "celo": "celo",
    "gnosis": "gnosis",
    "xdai": "gnosis",
    "moonbeam": "moonbeam",
    "moonriver": "moonriver",
    "kava": "kava",
    "metis": "metis",
    "aurora": "aurora",
    "harmony": "harmony",
    "boba": "boba",
    "klaytn": "klaytn",
    "heco": "huobi-eco-chain",
    "okc": "okex-chain",
    "berachain": "berachain",
    "hyperliquid": "hyperliquid",
}

# GeckoTerminal network identifiers
_GT_CHAIN_SLUGS: dict[str, str] = {
    "ethereum": "eth",
    "eth": "eth",
    "bsc": "bsc",
    "binance": "bsc",
    "binance-smart-chain": "bsc",
    "polygon": "polygon_pos",
    "matic": "polygon_pos",
    "arbitrum": "arbitrum",
    "arbitrum-one": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "avalanche": "avax",
    "avax": "avax",
    "fantom": "ftm",
    "ftm": "ftm",
    "cronos": "cro",
    "solana": "solana",
    "sol": "solana",
    "sui": "sui-network",
    "tron": "tron",
    "trx": "tron",
    "ton": "ton",
    "aptos": "aptos",
    "linea": "linea",
    "scroll": "scroll",
    "zksync": "zksync",
    "blast": "blast",
    "mantle": "mantle",
    "celo": "celo",
    "gnosis": "xdai",
    "xdai": "xdai",
    "moonbeam": "moonbeam",
    "moonriver": "moonriver",
    "kava": "kava",
    "metis": "metis",
    "aurora": "aurora",
    "klaytn": "klay-token",
    "berachain": "berachain",
}


def get_ds_slug(chain_name: str) -> str | None:
    return _DS_CHAIN_SLUGS.get((chain_name or "").strip().lower())


def get_gt_slug(chain_name: str) -> str | None:
    return _GT_CHAIN_SLUGS.get((chain_name or "").strip().lower())


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TokenPrice:
    """A contract address + chain pair, enriched with price data in-place."""

    address: str
    chain: str  # lowercase chain name (e.g. "ethereum")
    price_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    # optional metadata passed through for caller convenience
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rate limiter (GeckoTerminal: 30 req/min → 1 req/2 s)
# ---------------------------------------------------------------------------


class _AsyncRateLimiter:
    """Token-bucket rate limiter for async code."""

    def __init__(self, rate: float, per: float = 1.0) -> None:
        self._rate = rate
        self._per = per
        self._allowance = rate
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        import time

        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._allowance += elapsed * (self._rate / self._per)
            if self._allowance > self._rate:
                self._allowance = self._rate
            if self._allowance < 1.0:
                wait = (1.0 - self._allowance) * (self._per / self._rate)
                await asyncio.sleep(wait)
                self._allowance = 0.0
            else:
                self._allowance -= 1.0


_gecko_terminal_limiter = _AsyncRateLimiter(rate=30, per=60)


# ---------------------------------------------------------------------------
# Default browser-like headers (avoids trivial bot detection)
# ---------------------------------------------------------------------------

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Tier 1: DexScreener
# ---------------------------------------------------------------------------

_DS_BASE = "https://api.dexscreener.com/tokens/v1"


async def _enrich_dexscreener(
    tokens: list[TokenPrice],
    session: aiohttp.ClientSession,
) -> None:
    """Tier 1: DexScreener tokens API — batch price lookup (up to 30 per request)."""
    by_network: dict[str, list[TokenPrice]] = {}
    for t in tokens:
        slug = get_ds_slug(t.chain)
        if slug:
            by_network.setdefault(slug, []).append(t)

    if not by_network:
        return

    async def _fetch_network(network: str, batch_tokens: list[TokenPrice]) -> None:
        for i in range(0, len(batch_tokens), 30):
            batch = batch_tokens[i : i + 30]
            addresses = ",".join(t.address for t in batch)
            url = f"{_DS_BASE}/{network}/{addresses}"
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with session.get(
                    url, timeout=timeout, headers=_BROWSER_HEADERS
                ) as resp:
                    if resp.status != 200:
                        logger.debug(
                            "DexScreener %s HTTP %s for %s",
                            network,
                            resp.status,
                            addresses[:40],
                        )
                        continue
                    data = await resp.json(content_type=None)
            except Exception as exc:
                logger.debug(
                    "DexScreener fetch failed for %s batch %d: %s", network, i, exc
                )
                continue

            if not isinstance(data, list):
                continue

            # Build best-liquidity price map for base and quote tokens
            addr_price: dict[str, tuple[float, float]] = {}  # addr → (price, liq)
            addr_total_liq: dict[str, float] = {}

            for pair in data:
                base_addr = (pair.get("baseToken") or {}).get("address", "").lower()
                quote_addr = (pair.get("quoteToken") or {}).get("address", "").lower()
                price_usd_str = pair.get("priceUsd")
                price_native_str = pair.get("priceNative")
                liq = (pair.get("liquidity") or {}).get("usd") or 0

                if not price_usd_str:
                    continue
                try:
                    base_price = float(price_usd_str)
                except (ValueError, TypeError):
                    continue

                if base_addr:
                    prev = addr_price.get(base_addr)
                    if prev is None or liq > prev[1]:
                        addr_price[base_addr] = (base_price, liq)
                    addr_total_liq[base_addr] = addr_total_liq.get(base_addr, 0) + liq

                if quote_addr and price_native_str:
                    try:
                        pn = float(price_native_str)
                        if pn > 0:
                            quote_price = base_price / pn
                            prev = addr_price.get(quote_addr)
                            if prev is None or liq > prev[1]:
                                addr_price[quote_addr] = (quote_price, liq)
                            addr_total_liq[quote_addr] = (
                                addr_total_liq.get(quote_addr, 0) + liq
                            )
                    except (ValueError, TypeError, ZeroDivisionError):
                        pass

            for t in batch:
                key = t.address.lower()
                entry = addr_price.get(key)
                if entry is not None:
                    t.price_usd = entry[0]
                total_liq = addr_total_liq.get(key)
                if total_liq:
                    t.liquidity_usd = total_liq

    await asyncio.gather(
        *[_fetch_network(network, batch) for network, batch in by_network.items()]
    )


# ---------------------------------------------------------------------------
# Tier 2: GeckoTerminal
# ---------------------------------------------------------------------------

_GT_BASE = "https://api.geckoterminal.com/api/v2/simple/networks"


async def _enrich_gecko_terminal(
    tokens: list[TokenPrice],
    session: aiohttp.ClientSession,
) -> None:
    """Tier 2: GeckoTerminal simple token price API — batch up to 30."""
    by_network: dict[str, list[TokenPrice]] = {}
    for t in tokens:
        slug = get_gt_slug(t.chain)
        if slug:
            by_network.setdefault(slug, []).append(t)

    if not by_network:
        return

    async def _fetch_network(network: str, batch_tokens: list[TokenPrice]) -> None:
        for i in range(0, len(batch_tokens), 30):
            batch = batch_tokens[i : i + 30]
            addresses = ",".join(t.address for t in batch)
            url = f"{_GT_BASE}/{network}/token_price/{addresses}"

            await _gecko_terminal_limiter.acquire()
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with session.get(
                    url, timeout=timeout, headers=_BROWSER_HEADERS
                ) as resp:
                    if resp.status == 429:
                        logger.debug(
                            "GeckoTerminal 429 for %s, retrying after 3s", network
                        )
                        await asyncio.sleep(3)
                        await _gecko_terminal_limiter.acquire()
                        async with session.get(
                            url, timeout=timeout, headers=_BROWSER_HEADERS
                        ) as resp2:
                            if resp2.status != 200:
                                continue
                            data = await resp2.json(content_type=None)
                    elif resp.status != 200:
                        logger.debug(
                            "GeckoTerminal %s HTTP %s for %s",
                            network,
                            resp.status,
                            addresses[:40],
                        )
                        continue
                    else:
                        data = await resp.json(content_type=None)
            except Exception as exc:
                logger.debug(
                    "GeckoTerminal fetch failed for %s batch %d: %s", network, i, exc
                )
                continue

            token_prices: dict = (
                (data.get("data") or {}).get("attributes", {}).get("token_prices", {})
            )
            for t in batch:
                price_str = token_prices.get(t.address) or token_prices.get(
                    t.address.lower()
                )
                if price_str:
                    try:
                        t.price_usd = float(price_str)
                    except (ValueError, TypeError):
                        pass

    await asyncio.gather(
        *[_fetch_network(network, batch) for network, batch in by_network.items()]
    )


# ---------------------------------------------------------------------------
# Tier 3: DEXTools
# ---------------------------------------------------------------------------

_DT_SEARCH_URL = "https://core-api.dextools.io/search/pool"
_DT_HEADERS = {
    **_BROWSER_HEADERS,
    "X-API-Version": "1",
    "Dext-Client-Environment": "beta-prod",
    "Referer": "https://www.dextools.io/",
    "Origin": "https://www.dextools.io",
}


async def _enrich_dextools(
    tokens: list[TokenPrice],
    session: aiohttp.ClientSession,
) -> None:
    """Tier 3: DEXTools search API — per-address, last resort (max 15)."""

    sem = asyncio.Semaphore(5)

    async def _fetch_one(t: TokenPrice) -> None:
        async with sem:
            params = {"searchTerm": t.address, "strict": "true", "results": "1"}
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with session.get(
                    _DT_SEARCH_URL, params=params, headers=_DT_HEADERS, timeout=timeout
                ) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json(content_type=None)
            except Exception as exc:
                logger.debug("DEXTools fetch failed for %s: %s", t.address, exc)
                return

            results = data.get("results") or []
            if not results:
                return
            price = (results[0].get("price") or {}).get("usd")
            if price is not None:
                try:
                    t.price_usd = float(price)
                except (ValueError, TypeError):
                    pass

    await asyncio.gather(*[_fetch_one(t) for t in tokens])


# ---------------------------------------------------------------------------
# Native coin price via Binance (no API key, no contract address needed)
# ---------------------------------------------------------------------------

_BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"

# Map of canonical coin symbol → Binance trading pair symbol
_BINANCE_PAIRS: dict[str, str] = {
    "TRX": "TRXUSDT",
    "TON": "TONUSDT",
    "SUI": "SUIUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "ETH": "ETHUSDT",
    "BTC": "BTCUSDT",
    "AVAX": "AVAXUSDT",
    "MATIC": "MATICUSDT",
    "POL": "POLUSDT",
    "ARB": "ARBUSDT",
    "OP": "OPUSDT",
    "APT": "APTUSDT",
    "NEAR": "NEARUSDT",
    "FTM": "FTMUSDT",
    "ATOM": "ATOMUSDT",
    "DOT": "DOTUSDT",
    "ADA": "ADAUSDT",
    "XRP": "XRPUSDT",
    "LTC": "LTCUSDT",
    "LINK": "LINKUSDT",
    "UNI": "UNIUSDT",
    "AAVE": "AAVEUSDT",
}


async def get_native_price(symbol: str, session: aiohttp.ClientSession) -> float:
    """
    Fetch USD price for a native chain coin (no contract address) via Binance.

    Supports: TRX, TON, SUI, SOL, BNB, ETH, BTC, AVAX, MATIC, ARB, OP, etc.
    Returns 0.0 if the symbol is unknown or the request fails.
    """
    pair = _BINANCE_PAIRS.get(symbol.upper())
    if not pair:
        logger.debug("get_native_price: no Binance pair for symbol %s", symbol)
        return 0.0
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with session.get(
            _BINANCE_TICKER_URL,
            params={"symbol": pair},
            timeout=timeout,
            headers={"Accept": "application/json"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            return float(data.get("price") or 0)
    except Exception as exc:
        logger.debug("Binance price fetch failed for %s (%s): %s", symbol, pair, exc)
        return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def enrich_prices(
    tokens: list[TokenPrice],
    session: aiohttp.ClientSession,
    *,
    dextools_limit: int = 15,
) -> None:
    """
    3-tier parallel price enrichment.  Mutates ``tokens`` in-place.

    Priority: DexScreener > GeckoTerminal > DEXTools.

    Args:
        tokens:          List of TokenPrice objects (address + chain required).
        session:         Shared aiohttp.ClientSession from the caller.
        dextools_limit:  Max number of contracts to send to DEXTools (Tier 3).
                         Set to 0 to skip Tier 3 entirely.
    """
    if not tokens:
        return

    # Snapshot copies for DS and GT so we can merge by priority afterwards
    def _clone(t: TokenPrice) -> TokenPrice:
        return TokenPrice(address=t.address, chain=t.chain, extra=dict(t.extra))

    ds_copy = [_clone(t) for t in tokens]
    gt_copy = [_clone(t) for t in tokens]

    # Run Tier 1 + Tier 2 in parallel
    await asyncio.gather(
        _enrich_dexscreener(ds_copy, session),
        _enrich_gecko_terminal(gt_copy, session),
    )

    # Merge: DS wins over GT for price; DS also wins for liquidity
    for i, t in enumerate(tokens):
        if ds_copy[i].price_usd is not None:
            t.price_usd = ds_copy[i].price_usd
        elif gt_copy[i].price_usd is not None:
            t.price_usd = gt_copy[i].price_usd
        if ds_copy[i].liquidity_usd is not None:
            t.liquidity_usd = ds_copy[i].liquidity_usd

    # Tier 3: DEXTools — only for remaining gaps, capped at dextools_limit
    if dextools_limit > 0:
        remaining = [t for t in tokens if t.price_usd is None]
        if remaining:
            await _enrich_dextools(remaining[:dextools_limit], session)
