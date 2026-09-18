import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp

from app.core.config import Settings
from app.schemas.balance import ServiceBalanceSchema
from app.services.ccxt_manager import CCXTManager
from app.services.exchange_rest import (
    BinanceRestBalanceGateway,
    BybitRestBalanceGateway,
    CCXTBalanceGateway,
    ExchangeBalanceGatewayRegistry,
    GateIoRestBalanceGateway,
    KucoinRestBalanceGateway,
    MexcRestBalanceGateway,
    OkxRestBalanceGateway,
)


class ExchangeTransportConfigTests(unittest.TestCase):
    def test_exchange_transport_overrides_map_normalizes_values(self):
        settings = Settings(exchange_transport_overrides='{"BiNaNcE":"REST","okx":"ccxt"}')

        self.assertEqual(
            settings.exchange_transport_overrides_map,
            {"binance": "rest", "okx": "ccxt"},
        )

    def test_get_active_exchanges_skips_disabled_overrides(self):
        settings = Settings(
            binance_api_key="a",
            bitget_api_key="",
            bybit_api_key="",
            gateio_api_key="",
            htx_api_key="",
            kucoin_api_key="",
            mexc_api_key="",
            okx_api_key="b",
            bitmart_api_key="",
            poloniex_api_key="",
            lbank_api_key="",
            coinex_api_key="",
            bingx_api_key="",
            xt_api_key="",
            exchange_transport_overrides='{"okx":"disabled"}',
        )

        self.assertEqual(settings.get_active_exchanges(), ["binance"])


class ExchangeBalanceGatewayRegistryTests(unittest.TestCase):
    def test_registry_uses_rest_gateway_when_exchange_is_enabled(self):
        settings = Settings(
            exchange_default_transport="ccxt",
            exchange_transport_overrides='{"binance":"rest","bybit":"rest"}',
        )
        registry = ExchangeBalanceGatewayRegistry(settings)

        self.assertIsInstance(registry.resolve("binance"), BinanceRestBalanceGateway)
        self.assertIsInstance(registry.resolve("bybit"), BybitRestBalanceGateway)

    def test_registry_uses_default_transport_when_supported(self):
        settings = Settings(
            exchange_default_transport="rest",
            exchange_transport_overrides="{}",
        )
        registry = ExchangeBalanceGatewayRegistry(settings)

        self.assertIsInstance(registry.resolve("okx"), OkxRestBalanceGateway)
        self.assertIsInstance(registry.resolve("gateio"), GateIoRestBalanceGateway)
        self.assertIsInstance(registry.resolve("kucoin"), KucoinRestBalanceGateway)
        self.assertIsInstance(registry.resolve("mexc"), MexcRestBalanceGateway)
        self.assertNotIsInstance(registry.resolve("htx"), CCXTBalanceGateway)
        self.assertNotIsInstance(registry.resolve("bitmart"), CCXTBalanceGateway)
        self.assertNotIsInstance(registry.resolve("poloniex"), CCXTBalanceGateway)
        self.assertNotIsInstance(registry.resolve("lbank"), CCXTBalanceGateway)
        self.assertNotIsInstance(registry.resolve("coinex"), CCXTBalanceGateway)
        self.assertNotIsInstance(registry.resolve("bingx"), CCXTBalanceGateway)
        self.assertNotIsInstance(registry.resolve("xt"), CCXTBalanceGateway)

    def test_registry_falls_back_to_ccxt_for_unsupported_rest_exchange(self):
        settings = Settings(
            exchange_default_transport="ccxt",
            exchange_transport_overrides='{"unknown":"rest"}',
        )
        registry = ExchangeBalanceGatewayRegistry(settings)

        self.assertIsInstance(registry.resolve("unknown"), CCXTBalanceGateway)

    def test_registry_can_resolve_rest_gateway_directly(self):
        settings = Settings(exchange_default_transport="ccxt")
        registry = ExchangeBalanceGatewayRegistry(settings)

        self.assertIsInstance(registry.resolve_rest("binance"), BinanceRestBalanceGateway)
        with self.assertRaises(KeyError):
            registry.resolve_rest("unknown")


class BaseRestGatewayProxyFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_request_retries_direct_on_proxy_disconnect(self):
        # Proxy egress is configured through the unified OUTBOUND_PROXY_URL setting; patch the
        # module-level settings so the test never depends on a developer's .env file.
        settings = Settings(outbound_proxy_url="http://user:secret@127.0.0.1:3128")
        gateway = BinanceRestBalanceGateway(settings)

        attempts: list[str | None] = []

        class _Response:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

            def raise_for_status(self_inner):
                return None

            async def json(self_inner):
                return {"ok": True}

        class _FailingResponse:
            async def __aenter__(self_inner):
                raise aiohttp.ServerDisconnectedError()

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        def fake_request(self, method, url, **kwargs):
            attempts.append(kwargs.get("proxy"))
            if kwargs.get("proxy"):
                return _FailingResponse()
            return _Response()

        with patch("app.core.http.settings", settings), patch(
            "aiohttp.ClientSession.request", new=fake_request
        ):
            payload = await gateway._json_request("GET", "https://example.com/api")

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], "http://user:secret@127.0.0.1:3128")
        self.assertIsNone(attempts[1])


class CCXTManagerGatewayDelegationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_balance_delegates_to_selected_gateway(self):
        manager = CCXTManager()
        expected = ServiceBalanceSchema(
            service="binance",
            accounts=[],
            assets=[],
            total_usd=0.0,
            updated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            actual=True,
        )
        gateway = AsyncMock()
        gateway.fetch_balance = AsyncMock(return_value=expected)
        registry = SimpleNamespace(resolve=lambda _: gateway)

        with patch.object(manager, "_get_balance_gateway_registry", return_value=registry):
            result = await manager.fetch_balance("binance")

        self.assertEqual(result.service, "binance")
        gateway.fetch_balance.assert_awaited_once_with("binance", manager)
