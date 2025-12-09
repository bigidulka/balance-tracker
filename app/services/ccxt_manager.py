import asyncio
import hashlib
import hmac
import logging
import os
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from io import StringIO
from typing import Optional

import aiohttp
import ccxt.pro as ccxtpro

from app.core.config import get_settings
from app.schemas.balance import AssetSchema, AccountBalanceSchema, ServiceBalanceSchema

logger = logging.getLogger(__name__)
settings = get_settings()


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


# NullWriter для подавления вывода
class NullWriter:
    """Поглощает весь вывод."""

    def write(self, text):
        pass

    def flush(self):
        pass


# Глобально подавляем stdout/stderr на уровне модуля
_null_writer = NullWriter()
sys.stdout = _null_writer
sys.stderr = _null_writer

# Восстанавливаем для логгера - используем оригинальные потоки
_original_stdout = sys.__stdout__
_original_stderr = sys.__stderr__

# Настраиваем handler который пишет в оригинальный stderr
import logging.handlers

_stream_handler = logging.StreamHandler(_original_stderr)
_stream_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logging.getLogger().addHandler(_stream_handler)
logging.getLogger().setLevel(logging.INFO)


# Конфигурация типов счетов для каждой биржи
# Все типы нормализуются к: spot, futures
EXCHANGE_ACCOUNT_TYPES: dict[str, list[dict]] = {
    "binance": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "spot", "params": {"type": "margin"}},  # margin -> spot
        {"type": "spot", "params": {"type": "funding"}},  # funding -> spot
        {"type": "futures", "params": {"type": "future"}},  # USDT-M futures
        {"type": "futures", "params": {"type": "delivery"}},  # COIN-M futures
    ],
    "okx": [
        {"type": "spot", "params": {"type": "trading"}},  # unified trading -> spot
        {"type": "spot", "params": {"type": "funding"}},  # funding -> spot
    ],
    "bybit": [
        {"type": "spot", "params": {"type": "unified"}},  # unified -> spot
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "spot", "params": {"type": "fund"}},  # fund -> spot
        {"type": "futures", "params": {"type": "contract"}},  # contract -> futures
    ],
    "bitget": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "spot", "params": {"type": "margin"}},  # margin -> spot
        {
            "type": "futures",
            "params": {"type": "swap", "subType": "linear"},
        },  # USDT futures
        {
            "type": "futures",
            "params": {"type": "swap", "subType": "inverse"},
        },  # COIN futures
    ],
    "gateio": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "spot", "params": {"type": "margin"}},  # margin -> spot
        {
            "type": "futures",
            "params": {"type": "swap", "settle": "usdt"},
        },  # USDT perpetual -> futures
    ],
    "htx": [
        {"type": "spot", "params": {}},  # только spot
    ],
    "kucoin": [
        {"type": "spot", "params": {"type": "trade"}},  # trade -> spot
        {"type": "spot", "params": {"type": "main"}},  # main -> spot
    ],
    "mexc": [
        {"type": "spot", "params": {}},  # default spot balance
        {"type": "futures", "params": {"type": "swap"}},  # futures
    ],
    "bitmart": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "spot", "params": {"type": "margin"}},  # margin -> spot
        {"type": "futures", "params": {"type": "swap"}},
    ],
    "poloniex": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "futures", "params": {"type": "swap"}},
    ],
    "lbank": [
        {"type": "spot", "params": {}},
    ],
    "coinex": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "spot", "params": {"type": "margin"}},  # margin -> spot
        {"type": "futures", "params": {"type": "swap"}},
    ],
    "bingx": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "futures", "params": {"type": "swap"}},
    ],
    "xt": [
        {"type": "spot", "params": {"type": "spot"}},
        {"type": "futures", "params": {"type": "swap"}},
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


class CCXTManager:
    def __init__(self):
        self._exchanges: dict[str, ccxtpro.Exchange] = {}
        self._lock = asyncio.Lock()
        self._tickers_cache: dict[str, dict] = {}
        self._tickers_cache_time: dict[str, datetime] = {}

    async def _fetch_bitmart_balance_direct(self) -> ServiceBalanceSchema:
        """Прямой запрос к BitMart API (обходит баг CCXT с currencies)"""
        config = settings.get_exchange_config("bitmart")
        api_key = config.get("apiKey")
        secret = config.get("secret")
        memo = config.get("uid", "")

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

        # Настройка прокси
        proxy = settings.proxy_url if settings.proxy_url else None

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()

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
            if proxy:
                exchange_config["aiohttp_proxy"] = proxy

            temp_exchange = ccxtpro.bitmart(exchange_config)
            try:
                tickers = await temp_exchange.fetch_tickers()
            except:
                pass
            finally:
                await temp_exchange.close()
        except:
            pass

        # Обрабатываем балансы
        assets: list[AssetSchema] = []
        total_usd = 0.0

        for item in wallet:
            available = float(item.get("available", 0))
            frozen = float(item.get("frozen", 0))
            total = available + frozen

            if total > 0:
                coin = item.get("id", item.get("currency", "UNKNOWN"))
                value_usd = await self._calculate_usd_value(coin, total, tickers)
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

        return ServiceBalanceSchema(
            service="bitmart",
            accounts=[account] if account else [],
            assets=assets,
            total_usd=total_usd,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

    async def _get_exchange(self, exchange_id: str) -> ccxtpro.Exchange:
        async with self._lock:
            if exchange_id not in self._exchanges:
                config = settings.get_exchange_config(exchange_id)
                if not config.get("apiKey"):
                    raise ValueError(f"No API key configured for {exchange_id}")

                exchange_class = getattr(ccxtpro, exchange_id, None)
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

                if settings.proxy_url:
                    exchange_config["aiohttp_proxy"] = settings.proxy_url
                    exchange_config["proxies"] = {
                        "http": settings.proxy_url,
                        "https": settings.proxy_url,
                    }

                self._exchanges[exchange_id] = exchange_class(exchange_config)

            return self._exchanges[exchange_id]

    async def _get_tickers(self, exchange: ccxtpro.Exchange, exchange_id: str) -> dict:
        """Получает тикеры с кэшированием на 60 секунд"""
        now = datetime.now(timezone.utc)
        cache_time = self._tickers_cache_time.get(exchange_id)

        if cache_time and (now - cache_time).total_seconds() < 60:
            return self._tickers_cache.get(exchange_id, {})

        try:
            tickers = await exchange.fetch_tickers()
            self._tickers_cache[exchange_id] = tickers
            self._tickers_cache_time[exchange_id] = now
            return tickers
        except Exception as e:
            logger.debug(f"Could not fetch tickers for {exchange_id}: {e}")
            return self._tickers_cache.get(exchange_id, {})

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
            balance = await exchange.fetch_balance(params)

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
                return None

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
            return None

    async def fetch_balance(self, exchange_id: str) -> ServiceBalanceSchema:
        """Получает балансы со всех типов счетов биржи"""

        # Используем прямой API для проблемных бирж
        if exchange_id in DIRECT_API_EXCHANGES:
            if exchange_id == "bitmart":
                return await self._fetch_bitmart_balance_direct()

        exchange = await self._get_exchange(exchange_id)

        try:
            # Получаем тикеры один раз
            tickers = await self._get_tickers(exchange, exchange_id)

            # Получаем конфигурацию счетов для биржи
            account_types = EXCHANGE_ACCOUNT_TYPES.get(
                exchange_id, [{"type": "spot", "params": {}}]
            )

            # Агрегируем по типам: spot и futures
            accounts_data: dict[str, dict[str, AssetSchema]] = {
                "spot": {},
                "futures": {},
            }
            accounts_totals: dict[str, float] = {
                "spot": 0.0,
                "futures": 0.0,
            }

            # Получаем балансы со всех типов счетов последовательно
            for acc_config in account_types:
                result = await self._fetch_account_balance(
                    exchange, exchange_id, acc_config, tickers
                )
                if isinstance(result, AccountBalanceSchema) and result.assets:
                    acc_type = result.account_type  # уже нормализован: spot или futures

                    # Агрегируем активы по типу счёта
                    for asset in result.assets:
                        if asset.coin in accounts_data[acc_type]:
                            existing = accounts_data[acc_type][asset.coin]
                            accounts_data[acc_type][asset.coin] = AssetSchema(
                                coin=asset.coin,
                                amount=existing.amount + asset.amount,
                                value_usd=existing.value_usd + asset.value_usd,
                            )
                        else:
                            accounts_data[acc_type][asset.coin] = asset

                    accounts_totals[acc_type] += result.total_usd

            # Формируем итоговые счета
            accounts: list[AccountBalanceSchema] = []
            all_assets: dict[str, AssetSchema] = {}
            total_usd = 0.0

            for acc_type in ["spot", "futures"]:
                if accounts_data[acc_type]:
                    assets_list = list(accounts_data[acc_type].values())
                    accounts.append(
                        AccountBalanceSchema(
                            account_type=acc_type,
                            assets=assets_list,
                            total_usd=accounts_totals[acc_type],
                        )
                    )
                    total_usd += accounts_totals[acc_type]

                    # Общий список активов
                    for asset in assets_list:
                        if asset.coin in all_assets:
                            existing = all_assets[asset.coin]
                            all_assets[asset.coin] = AssetSchema(
                                coin=asset.coin,
                                amount=existing.amount + asset.amount,
                                value_usd=existing.value_usd + asset.value_usd,
                            )
                        else:
                            all_assets[asset.coin] = asset

            return ServiceBalanceSchema(
                service=exchange_id,
                accounts=accounts,
                assets=list(all_assets.values()),
                total_usd=total_usd,
                updated_at=datetime.now(timezone.utc),
                actual=True,
            )

        except Exception as e:
            logger.error(f"Error fetching balance from {exchange_id}: {e}")
            raise

    async def _calculate_usd_value(
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

    async def fetch_all_balances(
        self, exchange_ids: Optional[list[str]] = None
    ) -> dict[str, ServiceBalanceSchema | Exception]:
        """Получает балансы со всех бирж"""
        if exchange_ids is None:
            exchange_ids = settings.get_active_exchanges()

        results = {}

        # Запускаем запросы последовательно чтобы избежать rate limit
        for exchange_id in exchange_ids:
            try:
                results[exchange_id] = await asyncio.wait_for(
                    self.fetch_balance(exchange_id), timeout=settings.request_timeout
                )
            except asyncio.TimeoutError:
                logger.error(f"Timeout fetching balance from {exchange_id}")
                results[exchange_id] = TimeoutError(f"Timeout for {exchange_id}")
            except Exception as e:
                logger.error(f"Failed to fetch balance from {exchange_id}: {e}")
                results[exchange_id] = e

        return results

    async def close_all(self):
        """Закрывает все соединения"""
        for exchange_id, exchange in self._exchanges.items():
            try:
                await exchange.close()
            except Exception as e:
                logger.error(f"Error closing {exchange_id}: {e}")
        self._exchanges.clear()
        self._tickers_cache.clear()
        self._tickers_cache_time.clear()


ccxt_manager = CCXTManager()
