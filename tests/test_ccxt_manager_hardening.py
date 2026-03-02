import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.services import ccxt_manager as ccxt_manager_module
from app.services.ccxt_manager import CCXTManager, _LimiterEntry


class KeyedLimiterEvictionTests(unittest.TestCase):
    def test_prune_removes_idle_entries_but_keeps_in_flight(self):
        manager = CCXTManager()
        manager._keyed_limiters_idle_ttl_seconds = 60

        manager._keyed_limiters = {
            "idle": _LimiterEntry(asyncio.Semaphore(1), last_used=100.0, in_flight=0),
            "active": _LimiterEntry(asyncio.Semaphore(1), last_used=100.0, in_flight=1),
            "fresh": _LimiterEntry(asyncio.Semaphore(1), last_used=195.0, in_flight=0),
        }

        manager._prune_keyed_limiters(now=200.0)

        self.assertNotIn("idle", manager._keyed_limiters)
        self.assertIn("active", manager._keyed_limiters)
        self.assertIn("fresh", manager._keyed_limiters)

    def test_prune_enforces_max_size_by_lru_for_removable_entries(self):
        manager = CCXTManager()
        manager._keyed_limiters_idle_ttl_seconds = 10_000
        manager._keyed_limiters_max_size = 2

        manager._keyed_limiters = {
            "oldest": _LimiterEntry(asyncio.Semaphore(1), last_used=10.0, in_flight=0),
            "middle": _LimiterEntry(asyncio.Semaphore(1), last_used=20.0, in_flight=0),
            "newest": _LimiterEntry(asyncio.Semaphore(1), last_used=30.0, in_flight=0),
        }

        manager._prune_keyed_limiters(now=40.0)

        self.assertEqual(len(manager._keyed_limiters), 2)
        self.assertNotIn("oldest", manager._keyed_limiters)
        self.assertIn("middle", manager._keyed_limiters)
        self.assertIn("newest", manager._keyed_limiters)


class BitmartTickerFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_balance_marks_actual_false_when_tickers_missing_for_non_stable_assets(self):
        manager = CCXTManager()
        api_config = {"apiKey": "key", "secret": "secret", "uid": "memo"}

        session_cm = AsyncMock()
        session = Mock()
        response_cm = AsyncMock()
        response = AsyncMock()

        response.json = AsyncMock(
            return_value={
                "code": 1000,
                "data": {
                    "wallet": [
                        {"id": "BTC", "available": "1", "frozen": "0"},
                    ]
                },
            }
        )
        response_cm.__aenter__.return_value = response
        session.get = Mock(return_value=response_cm)
        session_cm.__aenter__.return_value = session

        exchange = AsyncMock()
        exchange.fetch_tickers = AsyncMock(return_value={})
        exchange.close = AsyncMock()

        before = ccxt_manager_module.metrics_service.snapshot().get(
            "ccxt_bitmart_ticker_degraded_total", 0
        )

        with patch.object(
            ccxt_manager_module,
            "settings",
            SimpleNamespace(get_exchange_config=lambda *_: api_config, proxy_url=None),
        ):
            with patch.object(ccxt_manager_module.aiohttp, "ClientSession", return_value=session_cm):
                with patch.object(ccxt_manager_module.ccxtpro, "bitmart", return_value=exchange):
                    balance = await manager._fetch_bitmart_balance_direct()

        after = ccxt_manager_module.metrics_service.snapshot().get(
            "ccxt_bitmart_ticker_degraded_total", 0
        )

        self.assertFalse(balance.actual)
        self.assertEqual(after, before + 1)
        self.assertEqual(balance.assets[0].coin, "BTC")
        self.assertEqual(balance.assets[0].value_usd, 0.0)

    async def test_direct_balance_stablecoin_only_remains_actual_when_tickers_missing(self):
        manager = CCXTManager()
        api_config = {"apiKey": "key", "secret": "secret", "uid": "memo"}

        session_cm = AsyncMock()
        session = Mock()
        response_cm = AsyncMock()
        response = AsyncMock()

        response.json = AsyncMock(
            return_value={
                "code": 1000,
                "data": {
                    "wallet": [
                        {"id": "USDT", "available": "100", "frozen": "0"},
                    ]
                },
            }
        )
        response_cm.__aenter__.return_value = response
        session.get = Mock(return_value=response_cm)
        session_cm.__aenter__.return_value = session

        exchange = AsyncMock()
        exchange.fetch_tickers = AsyncMock(return_value={})
        exchange.close = AsyncMock()

        with patch.object(
            ccxt_manager_module,
            "settings",
            SimpleNamespace(get_exchange_config=lambda *_: api_config, proxy_url=None),
        ):
            with patch.object(ccxt_manager_module.aiohttp, "ClientSession", return_value=session_cm):
                with patch.object(ccxt_manager_module.ccxtpro, "bitmart", return_value=exchange):
                    balance = await manager._fetch_bitmart_balance_direct()

        self.assertTrue(balance.actual)
        self.assertEqual(balance.total_usd, 100.0)
        self.assertEqual(balance.assets[0].coin, "USDT")
