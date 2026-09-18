import asyncio
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from time import monotonic
from typing import Any, Awaitable, Callable, Optional, TypeVar

import aiohttp
import ccxt.async_support as ccxt_async
import ccxt.pro as ccxtpro

from app.core.http import build_proxy_url, is_http_proxy, is_socks_proxy, request_proxy_kwargs, session_kwargs

try:
    from ccxt.base.errors import NotSupported as CCXTNotSupported
except Exception:
    CCXTNotSupported = Exception

from app.core.config import get_settings
from app.schemas.balance import (
    AssetSchema,
    AccountBalanceSchema,
    ServiceBalanceSchema,
    TransactionSchema,
)
from app.services.metrics_service import metrics_service

logger = logging.getLogger(__name__)
settings = get_settings()

# CCXT renames and drops exchange ids between releases (gateio -> gate, and some REST-only
# venues have no Pro/WebSocket class at all). Resolve an exchange class across both
# namespaces so a missing class degrades one integration instead of failing the request.
_CCXT_EXCHANGE_ALIASES = {"gateio": "gate"}


def _ccxt_exchange_class(exchange_id: str):
    """Return an exchange class from CCXT Pro or the REST async namespace, if available."""

    for candidate in (exchange_id, _CCXT_EXCHANGE_ALIASES.get(exchange_id, exchange_id)):
        exchange_class = getattr(ccxtpro, candidate, None) or getattr(ccxt_async, candidate, None)
        if exchange_class is not None:
            return exchange_class
    return None


# Полностью отключаем debug output от ccxt и aiohttp
os.environ["CCXT_DEBUG"] = "0"

# Подавляем все HTTP debug логи
for logger_name in [
    "ccxt",
    "ccxtpro",
    "ccxt.base.exchange",
    "aiohttp",
    "aiohttp.client",
    "urllib3",
    "chardet",
]:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)
    logging.getLogger(logger_name).propagate = False


# Конфигурация source account types для каждой биржи. Labels are API-visible;
# dashboard spot/futures compatibility is handled separately by classification.
EXCHANGE_ACCOUNT_TYPES: dict[str, list[dict]] = {
    "binance": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "margin", "params": {"type": "margin"}},
        {"type": "funding", "params": {"type": "funding"}},
        {"type": "usdt_futures", "params": {"type": "future"}},
        {"type": "coin_futures", "params": {"type": "delivery"}},
    ],
    "okx": [
        {"type": "trading", "params": {"type": "trading"}},
        {"type": "funding", "params": {"type": "funding"}},
    ],
    "bybit": [
        # Bybit Unified Account - один запрос возвращает все балансы (spot + derivatives)
        {"type": "unified", "params": {"type": "unified"}},
    ],
    "bitget": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "margin", "params": {"type": "margin"}},
        {
            "type": "linear_futures",
            "params": {"type": "swap", "subType": "linear"},
        },
        {
            "type": "inverse_futures",
            "params": {"type": "swap", "subType": "inverse"},
        },
    ],
    "gateio": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "margin", "params": {"type": "margin"}},
        {
            "type": "usdt_futures",
            "params": {"type": "swap", "settle": "usdt"},
        },
    ],
    "htx": [
        {"type": "spot", "params": {}},  # только spot
    ],
    "kucoin": [
        {"type": "trade", "params": {"type": "trade"}},
        {"type": "main", "params": {"type": "main"}},
    ],
    "mexc": [
        {"type": "spot", "params": {}},  # default spot balance
        {"type": "usdt_futures", "params": {"type": "swap"}},
    ],
    "bitmart": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "margin", "params": {"type": "margin"}},
        {"type": "usdt_futures", "params": {"type": "swap"}},
    ],
    "poloniex": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "usdt_futures", "params": {"type": "swap"}},
    ],
    "lbank": [
        {"type": "spot", "params": {}},
    ],
    "coinex": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "margin", "params": {"type": "margin"}},
        {"type": "usdt_futures", "params": {"type": "swap"}},
    ],
    "bingx": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "usdt_futures", "params": {"type": "swap"}},
    ],
    "xt": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "usdt_futures", "params": {"type": "swap"}},
    ],
}

# Специальные опции для некоторых бирж
EXCHANGE_OPTIONS: dict[str, dict] = {
    "htx": {
        "fetchMarkets": ["spot"],  # Отключаем swap/futures markets - API недоступен
    },
}

# Биржи которые требуют прямого API запроса вместо CCXT
DIRECT_API_EXCHANGES = {"bitmart"}

RAW_BALANCE_STRATEGY_RAW = "raw"
RAW_BALANCE_STRATEGY_LEGACY = "legacy"
RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK = "raw_then_fallback"

# Пер-биржевой маршрут для raw-path получения баланса.
# Для бирж со специфичным парсингом в fetch_balance используем raw_then_fallback.
RAW_BALANCE_STRATEGIES: dict[str, str] = {
    "binance": RAW_BALANCE_STRATEGY_RAW,
    "okx": RAW_BALANCE_STRATEGY_RAW,
    "bybit": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "bitget": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "gateio": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "htx": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "kucoin": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "mexc": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "bitmart": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "poloniex": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "lbank": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "coinex": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "bingx": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
    "xt": RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK,
}

# Пер-биржевые кандидаты implicit методов (CCXT) для raw вызова.
# Для некоторых бирж часть методов может отсутствовать; тогда сработает fallback.
RAW_BALANCE_METHOD_CANDIDATES: dict[str, dict[str, list[str]]] = {
    "bybit": {
        "type=unified|subType=|settle=": ["privateGetV5AccountWalletBalance"],
    },
    "bitget": {
        "type=spot|subType=|settle=": [
            "privateGetSpotV1AccountAssets",
            "privateGetSpotV2SpotAccountAssets",
        ],
        "type=margin|subType=|settle=": ["privateGetMarginV1CrossAccountAssets"],
        "type=swap|subType=linear|settle=": ["privateGetMixV1AccountAccounts"],
        "type=swap|subType=inverse|settle=": ["privateGetMixV1AccountAccounts"],
    },
    "gateio": {
        "type=spot|subType=|settle=": ["privateGetSpotAccounts"],
        "type=margin|subType=|settle=": ["privateGetMarginAccounts"],
        "type=swap|subType=|settle=usdt": ["privateFuturesGetFuturesSettleAccounts"],
    },
    "htx": {
        "type=__default__|subType=|settle=": ["privateGetAccountAccounts"],
    },
    "kucoin": {
        "type=trade|subType=|settle=": ["privateGetAccounts"],
        "type=main|subType=|settle=": ["privateGetAccounts"],
    },
    "mexc": {
        "type=__default__|subType=|settle=": ["privateGetAccountInfo"],
        "type=swap|subType=|settle=": ["privateGetApiV1PrivateAccountAssets"],
    },
    "bitmart": {
        "type=spot|subType=|settle=": ["privateGetSpotV1Wallet"],
        "type=margin|subType=|settle=": ["privateGetAccountSubAccountV1Wallet"],
        "type=swap|subType=|settle=": ["privateGetContractPrivateAssetsDetail"],
    },
    "poloniex": {
        "type=spot|subType=|settle=": ["privateGetAccountsBalances"],
        "type=swap|subType=|settle=": ["privateGetV2Wallets"],
    },
    "lbank": {
        "type=__default__|subType=|settle=": ["privatePostAssetInfo"],
    },
    "coinex": {
        "type=spot|subType=|settle=": ["privateGetAssetsSpotBalance"],
        "type=margin|subType=|settle=": ["privateGetAssetsMarginBalance"],
        "type=swap|subType=|settle=": ["privateGetAssetsFuturesBalance"],
    },
    "bingx": {
        "type=spot|subType=|settle=": ["privateGetOpenApiSpotV1AccountBalance"],
        "type=swap|subType=|settle=": ["privateGetOpenApiSwapV2UserBalance"],
    },
    "xt": {
        "type=spot|subType=|settle=": ["privateGetV4Balance"],
        "type=swap|subType=|settle=": ["privateGetFutureUserV1Balance"],
    },
}

_PROXY_RETRYABLE_ERRORS = (
    aiohttp.ClientProxyConnectionError,
    aiohttp.ClientHttpProxyError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ServerConnectionError,
    asyncio.TimeoutError,
    TimeoutError,
)

T = TypeVar("T")


@dataclass
class _LimiterEntry:
    semaphore: asyncio.Semaphore
    last_used: float
    in_flight: int = 0


class CCXTManager:
    def __init__(self):
        self._exchanges: dict[str, ccxtpro.Exchange] = {}
        self._lock = asyncio.Lock()
        self._tickers_cache: dict[str, dict] = {}
        self._tickers_cache_time: dict[str, datetime] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._inflight_lock = asyncio.Lock()
        self._keyed_limiters: dict[str, _LimiterEntry] = {}
        self._keyed_limiters_lock = asyncio.Lock()
        self._keyed_limiters_max_size = 1024
        self._keyed_limiters_idle_ttl_seconds = 15 * 60
        self._balance_gateway_registry = None

    def _hash_value(self, raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def _proxy_hash(self) -> str:
        proxy = settings.proxy_url or ""
        return self._hash_value(proxy)

    def _api_key_hash(self, exchange_id: str) -> str:
        api_key = settings.get_exchange_config(exchange_id).get("apiKey", "")
        return self._hash_value(api_key)

    def _api_key_hash_from_config(self, config: dict[str, Any] | None) -> str:
        if not config:
            return "default"
        return self._hash_value(str(config.get("apiKey") or "default"))

    def _exchange_cache_key(
        self,
        exchange_id: str,
        config_override: dict[str, Any] | None = None,
    ) -> str:
        if not config_override:
            return exchange_id
        return f"{exchange_id}#{self._api_key_hash_from_config(config_override)}"

    def _routing_signature(self, exchange_id: str) -> str:
        return f"exchange:{exchange_id}:proxy:{self._proxy_hash()}:api:{self._api_key_hash(exchange_id)}"

    def _singleflight_key(self, operation: str, exchange_id: str, payload: dict) -> str:
        payload_hash = self._hash_value(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        )
        return (
            f"{operation}:{self._routing_signature(exchange_id)}:payload:{payload_hash}"
        )

    def _since_bucket(self, since: Optional[datetime]) -> int | None:
        if since is None:
            return None
        bucket_size = max(1, settings.transactions_singleflight_since_bucket_seconds)
        return int(since.timestamp()) // bucket_size

    def _get_balance_gateway_registry(self):
        if self._balance_gateway_registry is None:
            from app.services.exchange_rest import get_balance_gateway_registry

            self._balance_gateway_registry = get_balance_gateway_registry(settings)
        return self._balance_gateway_registry

    async def _singleflight(self, key: str, runner: Callable[[], Awaitable[T]]) -> T:
        if not settings.enable_ccxt_singleflight:
            return await runner()

        leader = False
        wait_started = perf_counter()

        async with self._inflight_lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(runner())
                self._inflight[key] = task
                leader = True
                metrics_service.inc("ccxt_singleflight_leader_total")
            else:
                metrics_service.inc("ccxt_singleflight_follower_total")

        if not leader:
            metrics_service.observe_duration(
                "ccxt_singleflight_wait_seconds", perf_counter() - wait_started
            )

        try:
            result = await asyncio.shield(task)
            return result
        except Exception:
            metrics_service.inc("ccxt_singleflight_error_total")
            raise
        finally:
            if leader:
                async with self._inflight_lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)

    def _prune_keyed_limiters(self, now: float) -> None:
        if not self._keyed_limiters:
            return

        idle_before = now - self._keyed_limiters_idle_ttl_seconds
        stale_keys = [
            key
            for key, entry in self._keyed_limiters.items()
            if entry.last_used < idle_before and entry.in_flight == 0
        ]
        for key in stale_keys:
            self._keyed_limiters.pop(key, None)

        max_size = self._keyed_limiters_max_size
        if len(self._keyed_limiters) <= max_size:
            return

        removable = [
            (key, entry.last_used)
            for key, entry in self._keyed_limiters.items()
            if entry.in_flight == 0
        ]
        removable.sort(key=lambda item: item[1])

        overflow = len(self._keyed_limiters) - max_size
        for key, _ in removable[:overflow]:
            self._keyed_limiters.pop(key, None)

    async def _run_with_backpressure(
        self, key: str, runner: Callable[[], Awaitable[T]]
    ) -> T:
        if not settings.enable_ccxt_keyed_backpressure:
            return await runner()

        wait_started = perf_counter()
        wait_timeout = max(0.05, settings.ccxt_backpressure_wait_timeout_seconds)

        async with self._keyed_limiters_lock:
            now = monotonic()
            self._prune_keyed_limiters(now)
            entry = self._keyed_limiters.get(key)
            if entry is None:
                entry = _LimiterEntry(
                    semaphore=asyncio.Semaphore(
                        max(1, settings.ccxt_keyed_parallelism)
                    ),
                    last_used=now,
                )
                self._keyed_limiters[key] = entry
            else:
                entry.last_used = now

        try:
            await asyncio.wait_for(entry.semaphore.acquire(), timeout=wait_timeout)
        except asyncio.TimeoutError as exc:
            metrics_service.inc("ccxt_backpressure_rejected_total")
            async with self._keyed_limiters_lock:
                current = self._keyed_limiters.get(key)
                if current is not None:
                    current.last_used = monotonic()
                    self._prune_keyed_limiters(current.last_used)
            raise TimeoutError(f"Backpressure timeout for {key}") from exc

        metrics_service.observe_duration(
            "ccxt_backpressure_wait_seconds", perf_counter() - wait_started
        )

        async with self._keyed_limiters_lock:
            current = self._keyed_limiters.get(key)
            if current is not None:
                current.in_flight += 1
                current.last_used = monotonic()

        try:
            return await runner()
        finally:
            entry.semaphore.release()
            async with self._keyed_limiters_lock:
                current = self._keyed_limiters.get(key)
                if current is not None:
                    current.in_flight = max(0, current.in_flight - 1)
                    current.last_used = monotonic()
                    self._prune_keyed_limiters(current.last_used)

    async def _run_exchange_call(
        self,
        exchange_id: str,
        operation: str,
        runner: Callable[[], Awaitable[T]],
    ) -> T:
        limit_key = f"{operation}:{self._routing_signature(exchange_id)}"

        async def _timed_runner() -> T:
            started = perf_counter()
            try:
                return await runner()
            finally:
                metrics_service.observe_duration(
                    "ccxt_call_duration_seconds", perf_counter() - started
                )

        return await self._run_with_backpressure(limit_key, _timed_runner)

    def _is_stablecoin(self, coin: str) -> bool:
        return coin.upper() in {
            "USDT",
            "USDC",
            "BUSD",
            "USD",
            "DAI",
            "TUSD",
            "USDP",
            "FDUSD",
            "USDD",
        }

    async def _get_json_with_proxy_fallback(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: int = 60,
    ) -> Any:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)

        async def _request(proxy: str | None) -> Any:
            async with aiohttp.ClientSession(**session_kwargs(timeout)) as session:
                kwargs = {"headers": headers}
                if proxy and is_http_proxy(proxy):
                    kwargs["proxy"] = proxy
                elif request_proxy_kwargs():
                    kwargs.update(request_proxy_kwargs())
                async with session.get(url, **kwargs) as resp:
                    resp.raise_for_status()
                    return await resp.json()

        if not settings.proxy_url:
            return await _request(None)

        try:
            return await _request(settings.proxy_url)
        except _PROXY_RETRYABLE_ERRORS as exc:
            logger.warning(
                "Proxy request failed for %s, retrying direct: %s",
                url,
                exc,
            )
            return await _request(None)

    async def _fetch_bitmart_balance_direct(
        self,
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        """Прямой запрос к BitMart API (обходит баг CCXT с currencies)"""
        config = config_override or settings.get_exchange_config("bitmart")
        api_key = config.get("apiKey")
        secret = config.get("secret")
        memo = config.get("uid") or config.get("password") or ""

        if not api_key or not secret:
            raise ValueError("No API key configured for bitmart")

        # Создаем подпись
        timestamp = str(int(time.time() * 1000))
        sign_str = f"{timestamp}#{memo}#"
        signature = hmac.new(
            secret.encode(), sign_str.encode(), hashlib.sha256
        ).hexdigest()

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

        url = "https://api-cloud.bitmart.com/spot/v1/wallet"

        data = await self._get_json_with_proxy_fallback(
            url,
            headers=headers,
            timeout_seconds=60,
        )

        if data.get("code") != 1000:
            raise Exception(f"BitMart API error: {data.get('message', data)}")

        wallet = data.get("data", {}).get("wallet", [])

        # Получаем тикеры для расчета USD стоимости
        tickers = {}
        try:
            # Создаем временный exchange только для тикеров
            exchange_config = {
                "apiKey": api_key,
                "secret": secret,
                "enableRateLimit": True,
                "timeout": 30000,
            }
            if memo:
                exchange_config["uid"] = memo
            proxy_url = build_proxy_url()
            if proxy_url:
                if is_socks_proxy(proxy_url):
                    exchange_config["socksProxy"] = proxy_url
                else:
                    exchange_config["aiohttp_proxy"] = proxy_url

            exchange_class = _ccxt_exchange_class("bitmart")
            if exchange_class is None:
                logger.warning(
                    "BitMart tickers unavailable: the installed CCXT exposes no bitmart "
                    "class, so non-stable assets stay unvalued"
                )
            else:
                temp_exchange = exchange_class(exchange_config)
                try:
                    tickers = await temp_exchange.fetch_tickers()
                except Exception as exc:
                    logger.warning("BitMart tickers fetch failed: %s", exc)
                finally:
                    await temp_exchange.close()
        except Exception as exc:
            logger.warning("BitMart ticker bootstrap failed: %s", exc)

        # Обрабатываем балансы
        assets: list[AssetSchema] = []
        total_usd = 0.0
        has_non_stable_assets = False
        has_zero_valued_non_stable_assets = False

        for item in wallet:
            available = float(item.get("available", 0))
            frozen = float(item.get("frozen", 0))
            total = available + frozen

            if total > 0:
                coin = item.get("id", item.get("currency", "UNKNOWN"))
                is_stablecoin = self._is_stablecoin(coin)
                if not is_stablecoin:
                    has_non_stable_assets = True

                value_usd = await self._calculate_usd_value(coin, total, tickers)
                if not is_stablecoin and value_usd == 0.0:
                    has_zero_valued_non_stable_assets = True

                total_usd += value_usd
                assets.append(AssetSchema(coin=coin, amount=total, value_usd=value_usd))

        account = (
            AccountBalanceSchema(
                account_type="spot",
                assets=assets,
                total_usd=total_usd,
            )
            if assets
            else None
        )

        degraded = (
            has_non_stable_assets and has_zero_valued_non_stable_assets and not tickers
        )
        if degraded:
            metrics_service.inc("ccxt_bitmart_ticker_degraded_total")
            logger.warning(
                "BitMart balance degraded: no tickers available, non-stable assets valued at 0"
            )

        return ServiceBalanceSchema(
            service="bitmart",
            accounts=[account] if account else [],
            assets=assets,
            total_usd=total_usd,
            updated_at=datetime.now(timezone.utc),
            actual=not degraded,
        )

    async def _get_exchange(
        self,
        exchange_id: str,
        config_override: dict[str, Any] | None = None,
    ) -> ccxtpro.Exchange:
        async with self._lock:
            cache_key = self._exchange_cache_key(exchange_id, config_override)
            if cache_key not in self._exchanges:
                config = config_override or settings.get_exchange_config(exchange_id)
                if not config.get("apiKey"):
                    raise ValueError(f"No API key configured for {exchange_id}")

                exchange_class = _ccxt_exchange_class(exchange_id)
                if exchange_class is None:
                    raise ValueError(f"Exchange {exchange_id} not supported by CCXT")

                exchange_config = {
                    "apiKey": config["apiKey"],
                    "secret": config["secret"],
                    "enableRateLimit": True,
                    "timeout": settings.request_timeout * 1000,
                    "verbose": False,
                    "options": {
                        "defaultType": "spot",
                        "adjustForTimeDifference": True,
                        **EXCHANGE_OPTIONS.get(
                            exchange_id, {}
                        ),  # Специальные опции для биржи
                    },
                }

                if config.get("password"):
                    exchange_config["password"] = config["password"]
                if config.get("uid"):
                    exchange_config["uid"] = config["uid"]

                proxy_url = build_proxy_url()
                if proxy_url:
                    if is_socks_proxy(proxy_url):
                        exchange_config["socksProxy"] = proxy_url
                    else:
                        exchange_config["aiohttp_proxy"] = proxy_url
                        exchange_config["proxies"] = {
                            "http": proxy_url,
                            "https": proxy_url,
                        }

                self._exchanges[cache_key] = exchange_class(exchange_config)

            return self._exchanges[cache_key]

    async def _get_tickers(self, exchange: ccxtpro.Exchange, exchange_id: str) -> dict:
        """Получает тикеры с кэшированием на 60 секунд"""
        now = datetime.now(timezone.utc)
        cache_time = self._tickers_cache_time.get(exchange_id)

        if cache_time and (now - cache_time).total_seconds() < 60:
            metrics_service.inc("ccxt_tickers_cache_hit_total")
            return self._tickers_cache.get(exchange_id, {})

        metrics_service.inc("ccxt_tickers_cache_miss_total")

        async def _load_tickers() -> dict:
            try:
                tickers = await self._run_exchange_call(
                    exchange_id,
                    "tickers",
                    lambda: exchange.fetch_tickers(),
                )
                self._tickers_cache[exchange_id] = tickers
                self._tickers_cache_time[exchange_id] = datetime.now(timezone.utc)
                return tickers
            except Exception as e:
                logger.debug(f"Could not fetch tickers for {exchange_id}: {e}")
                return self._tickers_cache.get(exchange_id, {})

        key = self._singleflight_key("tickers", exchange_id, {"ttl": 60})
        return await self._singleflight(key, _load_tickers)

    def _raw_balance_route_key(self, params: dict) -> str:
        request_params = dict(params)
        balance_type = request_params.get("type", "__default__")
        sub_type = request_params.get("subType", "")
        settle = request_params.get("settle", "")
        return f"type={balance_type}|subType={sub_type}|settle={settle}"

    def _is_valid_balance_payload(self, balance: Any) -> bool:
        return isinstance(balance, dict) and isinstance(balance.get("total", {}), dict)

    async def _call_raw_implicit_method(
        self,
        exchange: ccxtpro.Exchange,
        exchange_id: str,
        operation: str,
        method_name: str,
        query: dict,
    ) -> Any:
        method = getattr(exchange, method_name)
        return await self._run_exchange_call(
            exchange_id,
            operation,
            lambda: method(query),
        )

    async def _fetch_account_balance_via_raw_implicit_generic(
        self,
        exchange: ccxtpro.Exchange,
        exchange_id: str,
        params: dict,
    ) -> dict:
        request_params = dict(params)
        route_key = self._raw_balance_route_key(request_params)
        candidates_by_route = RAW_BALANCE_METHOD_CANDIDATES.get(exchange_id, {})
        candidate_methods = candidates_by_route.get(
            route_key
        ) or candidates_by_route.get("type=__default__|subType=|settle=", [])

        if not candidate_methods:
            raise ValueError(
                f"Raw implicit balance route is not configured for {exchange_id}"
            )

        parse_params = dict(request_params)
        parse_method = getattr(exchange, "parse_balance", None)
        safe_method = getattr(exchange, "safe_balance", None)
        if not callable(parse_method) or not callable(safe_method):
            raise AttributeError(
                f"Missing parse/safe balance methods for {exchange_id}"
            )

        last_error: Exception | None = None
        for method_name in candidate_methods:
            try:
                response = await self._call_raw_implicit_method(
                    exchange,
                    exchange_id,
                    f"raw_balance_{method_name}",
                    method_name,
                    request_params,
                )
                parsed = parse_method(response, parse_params)
                balance = safe_method(parsed)
                if not self._is_valid_balance_payload(balance):
                    raise ValueError(
                        f"Incompatible raw balance payload for {exchange_id}:{method_name}"
                    )
                return balance
            except (AttributeError, ValueError) as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise last_error

        raise ValueError(f"Raw implicit balance call failed for {exchange_id}")

    async def _fetch_account_balance_via_raw_ccxt(
        self,
        exchange: ccxtpro.Exchange,
        exchange_id: str,
        params: dict,
    ) -> dict:
        request_params = dict(params)

        if exchange_id == "binance":
            balance_type = request_params.get("type", "spot")
            query = {k: v for k, v in request_params.items() if k != "type"}

            if balance_type == "funding":
                response = await self._run_exchange_call(
                    exchange_id,
                    "raw_balance_funding",
                    lambda: exchange.sapiPostAssetGetFundingAsset(query),
                )
                parsed = exchange.parse_balance_custom(response, "funding")
                balance = exchange.safe_balance(parsed)
                if not self._is_valid_balance_payload(balance):
                    raise ValueError(
                        "Incompatible raw funding balance payload for binance"
                    )
                return balance

            if balance_type == "margin":
                response = await self._run_exchange_call(
                    exchange_id,
                    "raw_balance_margin",
                    lambda: exchange.sapiGetMarginAccount(query),
                )
                parsed = exchange.parse_balance_custom(response, "margin")
                balance = exchange.safe_balance(parsed)
                if not self._is_valid_balance_payload(balance):
                    raise ValueError(
                        "Incompatible raw margin balance payload for binance"
                    )
                return balance

            if balance_type in {"future", "swap", "linear"}:
                response = await self._run_exchange_call(
                    exchange_id,
                    "raw_balance_linear",
                    lambda: exchange.fapiPrivateV3GetAccount(query),
                )
                parsed = exchange.parse_balance_custom(response, "linear")
                balance = exchange.safe_balance(parsed)
                if not self._is_valid_balance_payload(balance):
                    raise ValueError(
                        "Incompatible raw linear balance payload for binance"
                    )
                return balance

            if balance_type in {"delivery", "inverse"}:
                response = await self._run_exchange_call(
                    exchange_id,
                    "raw_balance_inverse",
                    lambda: exchange.dapiPrivateGetAccount(query),
                )
                parsed = exchange.parse_balance_custom(response, "inverse")
                balance = exchange.safe_balance(parsed)
                if not self._is_valid_balance_payload(balance):
                    raise ValueError(
                        "Incompatible raw inverse balance payload for binance"
                    )
                return balance

            response = await self._run_exchange_call(
                exchange_id,
                "raw_balance_spot",
                lambda: exchange.privateGetAccount(query),
            )
            parsed = exchange.parse_balance_custom(response, "spot")
            balance = exchange.safe_balance(parsed)
            if not self._is_valid_balance_payload(balance):
                raise ValueError("Incompatible raw spot balance payload for binance")
            return balance

        if exchange_id == "okx":
            balance_type = request_params.get("type", "trading")
            query = {k: v for k, v in request_params.items() if k != "type"}

            if balance_type == "funding":
                response = await self._run_exchange_call(
                    exchange_id,
                    "raw_balance_funding",
                    lambda: exchange.privateGetAssetBalances(query),
                )
            else:
                response = await self._run_exchange_call(
                    exchange_id,
                    "raw_balance_trading",
                    lambda: exchange.privateGetAccountBalance(query),
                )

            parsed = exchange.parse_balance_by_type(balance_type, response)
            balance = exchange.safe_balance(parsed)
            if not self._is_valid_balance_payload(balance):
                raise ValueError("Incompatible raw balance payload for okx")
            return balance

        if exchange_id in EXCHANGE_ACCOUNT_TYPES:
            return await self._fetch_account_balance_via_raw_implicit_generic(
                exchange, exchange_id, request_params
            )

        raise ValueError(f"Raw balance path is not supported for {exchange_id}")

    async def _fetch_account_balance(
        self,
        exchange: ccxtpro.Exchange,
        exchange_id: str,
        account_config: dict,
        tickers: dict,
    ) -> Optional[AccountBalanceSchema]:
        """Получает баланс одного типа счёта"""
        account_type = account_config["type"]
        params = account_config.get("params", {})

        try:
            strategy = RAW_BALANCE_STRATEGY_LEGACY
            if settings.enable_ccxt_raw_balance_path:
                strategy = RAW_BALANCE_STRATEGIES.get(
                    exchange_id, RAW_BALANCE_STRATEGY_LEGACY
                )

            balance: dict[str, Any]
            if strategy == RAW_BALANCE_STRATEGY_RAW:
                raw_started = perf_counter()
                balance = await self._fetch_account_balance_via_raw_ccxt(
                    exchange, exchange_id, params
                )
                metrics_service.observe_duration(
                    "ccxt_balance_raw_duration_seconds", perf_counter() - raw_started
                )
                metrics_service.inc("ccxt_balance_path_raw_total")
            elif strategy == RAW_BALANCE_STRATEGY_RAW_THEN_FALLBACK:
                try:
                    raw_started = perf_counter()
                    balance = await self._fetch_account_balance_via_raw_ccxt(
                        exchange, exchange_id, params
                    )
                    metrics_service.observe_duration(
                        "ccxt_balance_raw_duration_seconds",
                        perf_counter() - raw_started,
                    )
                    metrics_service.inc("ccxt_balance_path_raw_total")
                except (CCXTNotSupported, AttributeError, ValueError):
                    metrics_service.inc("ccxt_balance_path_fallback_total")
                    legacy_started = perf_counter()
                    balance = await self._run_exchange_call(
                        exchange_id,
                        "fetch_balance",
                        lambda: exchange.fetch_balance(params),
                    )
                    metrics_service.observe_duration(
                        "ccxt_balance_legacy_duration_seconds",
                        perf_counter() - legacy_started,
                    )
                    metrics_service.inc("ccxt_balance_path_legacy_total")
            else:
                legacy_started = perf_counter()
                balance = await self._run_exchange_call(
                    exchange_id,
                    "fetch_balance",
                    lambda: exchange.fetch_balance(params),
                )
                metrics_service.observe_duration(
                    "ccxt_balance_legacy_duration_seconds",
                    perf_counter() - legacy_started,
                )
                metrics_service.inc("ccxt_balance_path_legacy_total")

            assets: list[AssetSchema] = []
            total_usd = 0.0

            for coin, data in balance.get("total", {}).items():
                if data and float(data) > 0:
                    amount = float(data)
                    value_usd = await self._calculate_usd_value(coin, amount, tickers)
                    total_usd += value_usd
                    assets.append(
                        AssetSchema(coin=coin, amount=amount, value_usd=value_usd)
                    )

            if not assets:
                return AccountBalanceSchema(
                    account_type=account_type,
                    assets=[],
                    total_usd=0.0,
                )

            return AccountBalanceSchema(
                account_type=account_type,
                assets=assets,
                total_usd=total_usd,
            )

        except Exception as e:
            error_msg = str(e).lower()
            # Игнорируем ошибки для неподдерживаемых типов счетов
            if any(
                x in error_msg
                for x in [
                    "not support",
                    "not available",
                    "not enabled",
                    "does not have",
                    "permission",
                    "invalid",
                    "not found",
                    "disabled",
                    "margin trading",
                ]
            ):
                logger.debug(f"{exchange_id} {account_type}: {e}")
            else:
                logger.warning(f"{exchange_id} {account_type} error: {e}")
            return AccountBalanceSchema(
                account_type=account_type,
                assets=[],
                total_usd=0.0,
                error="account balance unavailable",
            )

    async def fetch_balance(
        self,
        exchange_id: str,
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        account_types = EXCHANGE_ACCOUNT_TYPES.get(
            exchange_id, [{"type": "spot", "params": {}}]
        )

        async def _fetch_balance_impl() -> ServiceBalanceSchema:
            gateway = self._get_balance_gateway_registry().resolve(exchange_id)
            if config_override is None:
                return await gateway.fetch_balance(exchange_id, self)
            return await gateway.fetch_balance(
                exchange_id,
                self,
                config_override=config_override,
            )

        return await _fetch_balance_impl()

    async def _fetch_balance_via_ccxt(
        self,
        exchange_id: str,
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        """Получает балансы со всех типов счетов биржи"""

        account_types = EXCHANGE_ACCOUNT_TYPES.get(
            exchange_id, [{"type": "spot", "params": {}}]
        )
        key = self._singleflight_key(
            "balance",
            exchange_id,
            {
                "account_types": account_types,
                "api": self._api_key_hash_from_config(config_override),
            },
        )

        async def _fetch_balance_impl() -> ServiceBalanceSchema:
            if exchange_id in DIRECT_API_EXCHANGES and exchange_id == "bitmart":
                return await self._run_exchange_call(
                    exchange_id,
                    "bitmart_direct_balance",
                    lambda: self._fetch_bitmart_balance_direct(
                        config_override=config_override,
                    ),
                )

            exchange = await self._get_exchange(
                exchange_id,
                config_override=config_override,
            )

            try:
                tickers = await self._get_tickers(exchange, exchange_id)

                accounts_data: dict[str, dict[str, AssetSchema]] = {}
                accounts_totals: dict[str, float] = {}
                account_results: list[AccountBalanceSchema] = []

                inter_account_delay = max(
                    0.0, settings.ccxt_inter_account_delay_seconds
                )
                for index, acc_config in enumerate(account_types):
                    result = await self._fetch_account_balance(
                        exchange, exchange_id, acc_config, tickers
                    )
                    if inter_account_delay > 0 and index < len(account_types) - 1:
                        await asyncio.sleep(inter_account_delay)
                    if isinstance(result, AccountBalanceSchema):
                        account_results.append(result)
                    if isinstance(result, AccountBalanceSchema) and result.assets:
                        acc_type = result.account_type
                        account_assets = accounts_data.setdefault(acc_type, {})
                        accounts_totals.setdefault(acc_type, 0.0)

                        for asset in result.assets:
                            if asset.coin in account_assets:
                                existing = account_assets[asset.coin]
                                account_assets[asset.coin] = AssetSchema(
                                    coin=asset.coin,
                                    amount=existing.amount + asset.amount,
                                    value_usd=existing.value_usd + asset.value_usd,
                                )
                            else:
                                account_assets[asset.coin] = asset

                        accounts_totals[acc_type] += result.total_usd

                accounts: list[AccountBalanceSchema] = []
                all_assets: dict[str, AssetSchema] = {}
                total_usd = 0.0
                warnings: list[str] = []
                account_errors = False

                # Gate.io unified account dedup: preserve the source labels but
                # make the mirrored futures view display-only for totals.
                unified_account_detected = False
                if (
                    exchange_id == "gateio"
                    and accounts_data.get("spot")
                    and accounts_data.get("usdt_futures")
                ):
                    spot_usdt = accounts_data["spot"].get("USDT")
                    futures_total_amount = accounts_totals.get("usdt_futures", 0.0)
                    if spot_usdt and futures_total_amount > 0:
                        spot_usdt_val = spot_usdt.value_usd if isinstance(spot_usdt, AssetSchema) else float(spot_usdt)
                        if abs(spot_usdt_val - futures_total_amount) / max(spot_usdt_val, futures_total_amount) < 0.05:
                            unified_account_detected = True
                            accounts_data["usdt_futures"] = dict(accounts_data["spot"])
                            accounts_totals["usdt_futures"] = accounts_totals["spot"]

                for acc_type, account_assets in accounts_data.items():
                    if account_assets:
                        assets_list = list(account_assets.values())
                        mirror_of = (
                            "spot"
                            if unified_account_detected and acc_type == "usdt_futures"
                            else None
                        )
                        accounts.append(
                            AccountBalanceSchema(
                                account_type=acc_type,
                                assets=assets_list,
                                total_usd=accounts_totals[acc_type],
                                mirror_of=mirror_of,
                            )
                        )
                        if mirror_of:
                            continue
                        else:
                            total_usd += accounts_totals[acc_type]

                        for asset in assets_list:
                            if asset.amount > 0 and asset.value_usd <= 0:
                                warnings.append(
                                    f"unvalued_asset:{acc_type}:{asset.coin}"
                                )
                            if asset.coin in all_assets:
                                existing = all_assets[asset.coin]
                                all_assets[asset.coin] = AssetSchema(
                                    coin=asset.coin,
                                    amount=existing.amount + asset.amount,
                                    value_usd=existing.value_usd + asset.value_usd,
                                )
                            else:
                                all_assets[asset.coin] = asset

                represented_types = {account.account_type for account in accounts}
                for result in account_results:
                    if result.account_type not in represented_types:
                        accounts.append(result)
                        represented_types.add(result.account_type)
                    if result.error:
                        account_errors = True
                        warnings.append(f"account_unavailable:{result.account_type}")


                return ServiceBalanceSchema(
                    service=exchange_id,
                    accounts=accounts,
                    assets=list(all_assets.values()),
                    total_usd=total_usd,
                    updated_at=datetime.now(timezone.utc),
                    actual=bool(accounts) and not account_errors,
                    warnings=sorted(set(warnings)),
                )

            except Exception as e:
                logger.error(f"Error fetching balance from {exchange_id}: {e}")
                raise

        return await self._singleflight(key, _fetch_balance_impl)

    def calculate_usd_value_sync(
        self, coin: str, amount: float, tickers: dict
    ) -> float:
        """Рассчитывает USD стоимость монеты"""
        coin_upper = coin.upper()

        # Стейблкоины
        stablecoins = {
            "USDT",
            "USDC",
            "BUSD",
            "USD",
            "DAI",
            "TUSD",
            "USDP",
            "FDUSD",
            "USDD",
        }
        if coin_upper in stablecoins:
            return amount

        # Пробуем найти цену через различные пары и форматы
        coin_lower = coin.lower()
        usd_pairs = [
            # Стандартный формат: COIN/USDT
            f"{coin}/USDT",
            f"{coin}/USDC",
            f"{coin}/USD",
            f"{coin}/BUSD",
            f"{coin}/FDUSD",
            f"{coin_upper}/USDT",
            f"{coin_upper}/USDC",
            f"{coin_upper}/USD",
            # Нижний регистр без слэша (HTX формат): coinusdt
            f"{coin_lower}usdt",
            f"{coin_lower}usdc",
            f"{coin_lower}usd",
            # Верхний регистр без слэша: COINUSDT
            f"{coin_upper}USDT",
            f"{coin_upper}USDC",
        ]

        for pair in usd_pairs:
            if pair in tickers:
                ticker = tickers[pair]
                price = ticker.get("last") or ticker.get("close")
                if price:
                    return amount * float(price)

        return 0.0

    async def _calculate_usd_value(
        self, coin: str, amount: float, tickers: dict
    ) -> float:
        return self.calculate_usd_value_sync(coin, amount, tickers)

    async def fetch_all_balances(
        self, exchange_ids: Optional[list[str]] = None
    ) -> dict[str, ServiceBalanceSchema | Exception]:
        """Получает балансы со всех бирж"""
        if exchange_ids is None:
            exchange_ids = settings.get_active_exchanges()

        max_exchanges = settings.ccxt_max_exchanges_per_cycle
        if max_exchanges > 0:
            exchange_ids = exchange_ids[:max_exchanges]

        results = {}
        per_exchange_timeout = max(0.1, settings.ccxt_balance_call_timeout_seconds)
        inter_exchange_delay = max(0.0, settings.ccxt_inter_exchange_delay_seconds)

        # Запускаем запросы последовательно чтобы избежать rate limit
        for index, exchange_id in enumerate(exchange_ids):
            try:
                results[exchange_id] = await asyncio.wait_for(
                    self.fetch_balance(exchange_id), timeout=per_exchange_timeout
                )
            except asyncio.TimeoutError:
                logger.error(f"Timeout fetching balance from {exchange_id}")
                results[exchange_id] = TimeoutError(f"Timeout for {exchange_id}")
            except Exception as e:
                logger.error(f"Failed to fetch balance from {exchange_id}: {e}")
                results[exchange_id] = e

            if inter_exchange_delay > 0 and index < len(exchange_ids) - 1:
                await asyncio.sleep(inter_exchange_delay)

        return results

    # ==================== Transaction Methods ====================

    def _normalize_transaction(
        self, raw_tx: dict, exchange_id: str, tx_type: str
    ) -> TransactionSchema:
        """Нормализует транзакцию из CCXT формата в нашу схему"""

        # Извлекаем tx_id - используем id из CCXT или генерируем из txid
        tx_id = raw_tx.get("id")
        if not tx_id:
            tx_id = (
                raw_tx.get("txid")
                or f"{exchange_id}_{tx_type}_{raw_tx.get('timestamp', 'unknown')}"
            )

        # Парсим timestamp
        tx_timestamp = None
        if raw_tx.get("timestamp"):
            try:
                tx_timestamp = datetime.fromtimestamp(
                    raw_tx["timestamp"] / 1000, tz=timezone.utc
                )
            except (ValueError, TypeError):
                pass

        # Нормализуем статус
        raw_status = raw_tx.get("status", "").lower()
        if raw_status in ("ok", "completed", "finished", "success"):
            status = "ok"
        elif raw_status in ("pending", "processing", "confirming"):
            status = "pending"
        elif raw_status in ("failed", "rejected", "error"):
            status = "failed"
        elif raw_status in ("canceled", "cancelled"):
            status = "canceled"
        else:
            status = "pending"

        # Извлекаем комиссию
        fee_data = raw_tx.get("fee", {}) or {}
        fee = fee_data.get("cost")
        fee_currency = fee_data.get("currency")

        return TransactionSchema(
            tx_id=str(tx_id),
            service=exchange_id,
            tx_type=tx_type,
            currency=raw_tx.get("currency", "UNKNOWN"),
            amount=float(raw_tx.get("amount", 0) or 0),
            fee=float(fee) if fee else 0.0,
            fee_currency=fee_currency,
            network=raw_tx.get("network"),
            address=raw_tx.get("address"),
            address_from=raw_tx.get("addressFrom"),
            address_to=raw_tx.get("addressTo"),
            tag=raw_tx.get("tag") or raw_tx.get("tagTo"),
            status=status,
            txid=raw_tx.get("txid"),
            tx_timestamp=tx_timestamp,
            notified=False,
        )

    async def fetch_deposits(
        self,
        exchange_id: str,
        since: Optional[datetime] = None,
        limit: int = 50,
        config_override: dict[str, Any] | None = None,
    ) -> list[TransactionSchema]:
        """Получает историю вводов (deposits) для биржи"""

        exchange = await self._get_exchange(
            exchange_id, config_override=config_override
        )

        # Проверяем поддержку метода
        if not exchange.has.get("fetchDeposits"):
            logger.debug(f"{exchange_id} does not support fetchDeposits")
            return []

        try:
            since_ts = int(since.timestamp() * 1000) if since else None

            raw_deposits = await self._run_exchange_call(
                exchange_id,
                "fetch_deposits",
                lambda: exchange.fetch_deposits(
                    code=None,
                    since=since_ts,
                    limit=limit,  # Все валюты
                ),
            )

            deposits = []
            for raw_tx in raw_deposits:
                try:
                    tx = self._normalize_transaction(raw_tx, exchange_id, "deposit")
                    deposits.append(tx)
                except Exception as e:
                    logger.warning(
                        f"Failed to normalize deposit from {exchange_id}: {e}"
                    )

            logger.info(f"Fetched {len(deposits)} deposits from {exchange_id}")
            return deposits

        except Exception as e:
            error_msg = str(e).lower()
            # Игнорируем ошибки для неподдерживаемых функций
            if any(
                x in error_msg
                for x in ["not support", "not available", "permission", "not found"]
            ):
                logger.debug(f"{exchange_id} fetchDeposits not available: {e}")
            else:
                logger.warning(f"Error fetching deposits from {exchange_id}: {e}")
            return []

    async def fetch_withdrawals(
        self,
        exchange_id: str,
        since: Optional[datetime] = None,
        limit: int = 50,
        config_override: dict[str, Any] | None = None,
    ) -> list[TransactionSchema]:
        """Получает историю выводов (withdrawals) для биржи"""

        exchange = await self._get_exchange(
            exchange_id, config_override=config_override
        )

        # Проверяем поддержку метода
        if not exchange.has.get("fetchWithdrawals"):
            logger.debug(f"{exchange_id} does not support fetchWithdrawals")
            return []

        try:
            since_ts = int(since.timestamp() * 1000) if since else None

            raw_withdrawals = await self._run_exchange_call(
                exchange_id,
                "fetch_withdrawals",
                lambda: exchange.fetch_withdrawals(
                    code=None,
                    since=since_ts,
                    limit=limit,  # Все валюты
                ),
            )

            withdrawals = []
            for raw_tx in raw_withdrawals:
                try:
                    tx = self._normalize_transaction(raw_tx, exchange_id, "withdrawal")
                    withdrawals.append(tx)
                except Exception as e:
                    logger.warning(
                        f"Failed to normalize withdrawal from {exchange_id}: {e}"
                    )

            logger.info(f"Fetched {len(withdrawals)} withdrawals from {exchange_id}")
            return withdrawals

        except Exception as e:
            error_msg = str(e).lower()
            # Игнорируем ошибки для неподдерживаемых функций
            if any(
                x in error_msg
                for x in ["not support", "not available", "permission", "not found"]
            ):
                logger.debug(f"{exchange_id} fetchWithdrawals not available: {e}")
            else:
                logger.warning(f"Error fetching withdrawals from {exchange_id}: {e}")
            return []

    async def fetch_all_transactions(
        self,
        exchange_id: str,
        since: Optional[datetime] = None,
        limit: int = 50,
        config_override: dict[str, Any] | None = None,
    ) -> list[TransactionSchema]:
        """Получает все транзакции (deposits + withdrawals) для биржи"""

        singleflight_key = self._singleflight_key(
            "transactions",
            exchange_id,
            {
                "since_bucket": self._since_bucket(since),
                "limit": limit,
                "api": self._api_key_hash_from_config(config_override),
            },
        )

        async def _fetch_transactions_impl() -> list[TransactionSchema]:
            deposits = await self.fetch_deposits(
                exchange_id,
                since,
                limit,
                config_override=config_override,
            )
            withdrawals = await self.fetch_withdrawals(
                exchange_id,
                since,
                limit,
                config_override=config_override,
            )

            all_transactions = deposits + withdrawals

            all_transactions.sort(
                key=lambda x: (
                    x.tx_timestamp or datetime.min.replace(tzinfo=timezone.utc)
                ),
                reverse=True,
            )
            return all_transactions

        return await self._singleflight(singleflight_key, _fetch_transactions_impl)

    async def fetch_transactions_all_exchanges(
        self,
        exchange_ids: Optional[list[str]] = None,
        since: Optional[datetime] = None,
        limit: int = 50,
    ) -> dict[str, list[TransactionSchema] | Exception]:
        """Получает транзакции со всех бирж"""

        if exchange_ids is None:
            exchange_ids = settings.get_active_exchanges()

        max_exchanges = settings.ccxt_max_exchanges_per_cycle
        if max_exchanges > 0:
            exchange_ids = exchange_ids[:max_exchanges]

        timeout_seconds = max(0.1, float(settings.request_timeout))
        parallelism = max(1, settings.exchange_parallelism)
        semaphore = asyncio.Semaphore(parallelism)

        async def _fetch_one(exchange_id: str):
            async with semaphore:
                try:
                    txs = await asyncio.wait_for(
                        self.fetch_all_transactions(exchange_id, since, limit),
                        timeout=timeout_seconds,
                    )
                    return exchange_id, txs
                except asyncio.TimeoutError:
                    logger.error(f"Timeout fetching transactions from {exchange_id}")
                    return exchange_id, TimeoutError(f"Timeout for {exchange_id}")
                except Exception as exc:
                    logger.error(
                        f"Failed to fetch transactions from {exchange_id}: {exc}"
                    )
                    return exchange_id, exc

        fetch_tasks = [_fetch_one(exchange_id) for exchange_id in exchange_ids]
        results: dict[str, list[TransactionSchema] | Exception] = {}

        for exchange_id, result in await asyncio.gather(*fetch_tasks):
            results[exchange_id] = result

        return results

    async def verify_credentials(
        self,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        api_password: str | None = None,
        api_uid: str | None = None,
    ) -> tuple[bool, str | None]:
        """Verify exchange API credentials by attempting fetch_balance.

        Returns (True, None) on success or (False, error_message) on failure.
        Uses a temporary (non-cached) exchange instance that is closed after the check.
        """
        exchange_class = getattr(ccxtpro, exchange_id, None)
        if exchange_class is None:
            return False, f"Exchange {exchange_id} is not supported"

        exchange_config: dict[str, Any] = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 15_000,
            "verbose": False,
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": True,
                **EXCHANGE_OPTIONS.get(exchange_id, {}),
            },
        }
        if api_password:
            exchange_config["password"] = api_password
        if api_uid:
            exchange_config["uid"] = api_uid
        proxy_url = build_proxy_url()
        if proxy_url:
            if is_socks_proxy(proxy_url):
                exchange_config["socksProxy"] = proxy_url
            else:
                exchange_config["aiohttp_proxy"] = proxy_url
                exchange_config["proxies"] = {
                    "http": proxy_url,
                    "https": proxy_url,
                }

        exchange = exchange_class(exchange_config)
        try:
            await exchange.fetch_balance({"type": "spot"})
            return True, None
        except ccxtpro.AuthenticationError as exc:
            return False, f"Authentication failed: {exc}"
        except ccxtpro.PermissionDenied as exc:
            return False, f"Permission denied: {exc}"
        except ccxtpro.ExchangeNotAvailable as exc:
            return False, f"Exchange unavailable: {exc}"
        except ccxtpro.NetworkError as exc:
            return False, f"Network error: {exc}"
        except Exception as exc:
            msg = str(exc)
            if "auth" in msg.lower() or "key" in msg.lower() or "sign" in msg.lower():
                return False, f"Authentication failed: {exc}"
            return False, f"Verification failed: {exc}"
        finally:
            try:
                await exchange.close()
            except Exception:
                pass

    async def close_all(self):
        """Закрывает все соединения"""
        if self._balance_gateway_registry is not None:
            try:
                await self._balance_gateway_registry.close_all()
            except Exception as e:
                logger.error(f"Error closing REST balance gateways: {e}")
            self._balance_gateway_registry = None

        for exchange_id, exchange in self._exchanges.items():
            try:
                await exchange.close()
            except Exception as e:
                logger.error(f"Error closing {exchange_id}: {e}")
        self._exchanges.clear()
        self._tickers_cache.clear()
        self._tickers_cache_time.clear()


ccxt_manager = CCXTManager()
