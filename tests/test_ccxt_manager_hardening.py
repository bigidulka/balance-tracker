import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.services import ccxt_manager as ccxt_manager_module
from app.services.ccxt_manager import (
    CCXTManager,
    EXCHANGE_ACCOUNT_TYPES,
    RAW_BALANCE_STRATEGIES,
    _LimiterEntry,
)


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


class RawBalanceRoutingContractTests(unittest.TestCase):
    def test_raw_balance_strategy_map_covers_all_exchanges(self):
        for exchange_id in EXCHANGE_ACCOUNT_TYPES:
            self.assertIn(exchange_id, RAW_BALANCE_STRATEGIES)

    def test_route_key_is_stable_for_account_params(self):
        manager = CCXTManager()
        self.assertEqual(
            manager._raw_balance_route_key({"type": "swap", "subType": "linear"}),
            "type=swap|subType=linear|settle=",
        )
        self.assertEqual(
            manager._raw_balance_route_key({}),
            "type=__default__|subType=|settle=",
        )


class RawBalanceFallbackBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_then_fallback_uses_legacy_and_updates_metrics(self):
        manager = CCXTManager()
        exchange = AsyncMock()
        exchange.fetch_balance = AsyncMock(return_value={"total": {"USDT": 5}})

        with patch.object(
            ccxt_manager_module,
            "settings",
            SimpleNamespace(enable_ccxt_raw_balance_path=True),
        ):
            with patch.object(
                manager,
                "_fetch_account_balance_via_raw_ccxt",
                new=AsyncMock(side_effect=AttributeError("missing method")),
            ):
                with patch.object(
                    manager,
                    "_run_exchange_call",
                    new=AsyncMock(return_value={"total": {"USDT": 5}}),
                ):
                    with patch.object(
                        manager,
                        "_calculate_usd_value",
                        new=AsyncMock(return_value=5.0),
                    ):
                        before = ccxt_manager_module.metrics_service.snapshot()
                        result = await manager._fetch_account_balance(
                            exchange,
                            "bybit",
                            {"type": "spot", "params": {"type": "unified"}},
                            tickers={},
                        )
                        after = ccxt_manager_module.metrics_service.snapshot()

        self.assertIsNotNone(result)
        self.assertEqual(result.total_usd, 5.0)
        self.assertEqual(
            after.get("ccxt_balance_path_fallback_total", 0),
            before.get("ccxt_balance_path_fallback_total", 0) + 1,
        )
        self.assertEqual(
            after.get("ccxt_balance_path_legacy_total", 0),
            before.get("ccxt_balance_path_legacy_total", 0) + 1,
        )

    async def test_legacy_path_when_raw_flag_is_disabled(self):
        manager = CCXTManager()
        exchange = AsyncMock()
        exchange.fetch_balance = AsyncMock(return_value={"total": {"USDT": 10}})

        with patch.object(
            ccxt_manager_module,
            "settings",
            SimpleNamespace(enable_ccxt_raw_balance_path=False),
        ):
            with patch.object(
                manager,
                "_run_exchange_call",
                new=AsyncMock(return_value={"total": {"USDT": 10}}),
            ) as run_call:
                with patch.object(
                    manager,
                    "_calculate_usd_value",
                    new=AsyncMock(return_value=10.0),
                ):
                    result = await manager._fetch_account_balance(
                        exchange,
                        "binance",
                        {"type": "spot", "params": {"type": "spot"}},
                        tickers={},
                    )

        self.assertIsNotNone(result)
        self.assertEqual(result.total_usd, 10.0)
        run_call.assert_awaited()


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
