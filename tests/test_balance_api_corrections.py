import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.balance import Integration, IntegrationSecret, Organization, SyncJob
from app.schemas.balance import AccountBalanceSchema, AssetSchema, ServiceBalanceSchema
from app.services.account_type_classification import (
    CANONICAL_ACCOUNT_TYPES,
    account_counts_toward_total,
    dashboard_account_bucket,
)
from app.services.ccxt_manager import CCXTManager, EXCHANGE_ACCOUNT_TYPES
from app.services.exchange_rest import ExchangeBalanceGatewayRegistry
from app.services.integrations.ccxt_provider import CCXTIntegrationProvider
from app.services.integrations.provider import ProviderRefreshResult
from app.services.sync_job_service import SyncJobService
from bot.services.balance import parse_balances
from bot.services.account_type_classification import (
    account_counts_toward_total as bot_account_counts_toward_total,
    dashboard_account_bucket as bot_dashboard_account_bucket,
)


EXPECTED_ACCOUNT_REGISTRY = {
    "binance": ["spot", "margin", "funding", "usdt_futures", "coin_futures"],
    "okx": ["trading", "funding"],
    "bybit": ["unified"],
    "bitget": ["spot", "margin", "linear_futures", "inverse_futures"],
    "gateio": ["spot", "margin", "usdt_futures"],
    "htx": ["spot"],
    "kucoin": ["trade", "main"],
    "mexc": ["spot", "usdt_futures"],
    "bitmart": ["spot", "margin", "usdt_futures"],
    "poloniex": ["spot", "usdt_futures"],
    "lbank": ["spot"],
    "coinex": ["spot", "margin", "usdt_futures"],
    "bingx": ["spot", "usdt_futures"],
    "xt": ["spot", "usdt_futures"],
}


class ExchangeAccountRegistryContractTests(unittest.TestCase):
    def test_all_configured_cex_and_every_account_mapping_are_canonical(self):
        self.assertEqual(set(EXCHANGE_ACCOUNT_TYPES), set(EXPECTED_ACCOUNT_REGISTRY))
        for exchange_id, expected_types in EXPECTED_ACCOUNT_REGISTRY.items():
            mappings = EXCHANGE_ACCOUNT_TYPES[exchange_id]
            self.assertEqual(
                [mapping["type"] for mapping in mappings],
                expected_types,
                msg=exchange_id,
            )
            self.assertEqual(len(mappings), len({mapping["type"] for mapping in mappings}))
            for mapping in mappings:
                self.assertIn(mapping["type"], CANONICAL_ACCOUNT_TYPES)
                self.assertIsInstance(mapping["params"], dict)

    def test_rest_registry_covers_the_same_fourteen_cex(self):
        registry = ExchangeBalanceGatewayRegistry(SimpleNamespace())
        self.assertEqual(registry.supported_rest_exchanges(), set(EXPECTED_ACCOUNT_REGISTRY))

    def test_dashboard_classification_keeps_legacy_totals_without_normalizing_api_labels(self):
        spot_labels = {"spot", "margin", "funding", "trading", "unified", "main", "trade"}
        futures_labels = {"usdt_futures", "coin_futures", "linear_futures", "inverse_futures"}
        self.assertEqual({dashboard_account_bucket(label) for label in spot_labels}, {"spot"})
        self.assertEqual({dashboard_account_bucket(label) for label in futures_labels}, {"futures"})
        self.assertTrue(account_counts_toward_total({"account_type": "unified"}))
        self.assertFalse(
            account_counts_toward_total(
                {"account_type": "usdt_futures", "mirror_of": "spot"}
            )
        )

    def test_bot_dashboard_buckets_canonical_labels_and_skips_mirrors(self):
        self.assertEqual(bot_dashboard_account_bucket("linear_futures"), "futures")
        self.assertFalse(
            bot_account_counts_toward_total(
                {"account_type": "coin_futures", "mirror_of": "spot"}
            )
        )
        parsed = parse_balances(
            {
                "services": [
                    {
                        "service": "binance",
                        "integration_id": 1,
                        "accounts": [
                            {"account_type": "margin", "assets": [], "total_usd": 10.0},
                            {"account_type": "usdt_futures", "assets": [], "total_usd": 20.0},
                            {
                                "account_type": "coin_futures",
                                "assets": [],
                                "total_usd": 10.0,
                                "mirror_of": "spot",
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(parsed["spot_total"], 10.0)
        self.assertEqual(parsed["futures_total"], 20.0)
        self.assertEqual(parsed["total"], 30.0)

class CCXTProviderCredentialPropagationTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_builds_and_passes_only_supported_config_override(self):
        integration = SimpleNamespace(
            id=42,
            exchange_code="okx",
            external_id=None,
            name="OKX",
            provider="ccxt",
        )
        balance = ServiceBalanceSchema(
            service="okx",
            accounts=[],
            assets=[],
            total_usd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        provider = CCXTIntegrationProvider()
        with patch(
            "app.services.integrations.ccxt_provider.ccxt_manager.fetch_balance",
            new=AsyncMock(return_value=balance),
        ) as fetch_balance:
            result = await provider.refresh(
                7,
                integration,
                {
                    "api_key": "queued-key",
                    "api_secret": "queued-secret",
                    "api_password": "queued-password",
                    "api_uid": "queued-uid",
                    "unrelated": "ignored",
                },
            )

        fetch_balance.assert_awaited_once_with(
            "okx",
            config_override={
                "apiKey": "queued-key",
                "secret": "queued-secret",
                "password": "queued-password",
                "uid": "queued-uid",
            },
        )
        self.assertEqual(result.status, "ok")
        self.assertNotIn("queued-secret", repr(result.data))
        self.assertNotIn("queued-password", repr(result.data))
        self.assertNotIn("queued-uid", repr(result.data))

    async def test_provider_requires_key_and_secret_before_any_fetch(self):
        integration = SimpleNamespace(
            id=42,
            exchange_code="okx",
            external_id=None,
            name="OKX",
            provider="ccxt",
        )
        provider = CCXTIntegrationProvider()
        with patch(
            "app.services.integrations.ccxt_provider.ccxt_manager.fetch_balance",
            new=AsyncMock(),
        ) as fetch_balance:
            result = await provider.refresh(7, integration, {"api_key": "queued-key"})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.message, "api_key and api_secret are required for ccxt provider")
        fetch_balance.assert_not_awaited()

    async def test_provider_rejects_empty_unverified_balance(self):
        integration = SimpleNamespace(
            id=42,
            exchange_code="binance",
            external_id=None,
            name="Binance",
            provider="ccxt",
        )
        balance = ServiceBalanceSchema(
            service="binance",
            accounts=[],
            assets=[],
            total_usd=0.0,
            updated_at=datetime.now(timezone.utc),
            actual=False,
        )
        provider = CCXTIntegrationProvider()
        with patch(
            "app.services.integrations.ccxt_provider.ccxt_manager.fetch_balance",
            new=AsyncMock(return_value=balance),
        ):
            result = await provider.refresh(
                7,
                integration,
                {"api_key": "queued-key", "api_secret": "queued-secret"},
            )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.message, "Exchange returned no verified balance data")


class ValuationCompletenessContractTests(unittest.TestCase):
    def test_rest_merge_reports_unvalued_positive_assets(self):
        from app.services.exchange_rest import _merge_accounts

        result = _merge_accounts(
            "coinex",
            [
                {
                    "account_type": "spot",
                    "tickers": {},
                    "assets": [{"coin": "UNKNOWN", "amount": 2.0}],
                }
            ],
            lambda _coin, _amount, _tickers: 0.0,
        )

        self.assertTrue(result.actual)
        self.assertEqual(result.warnings, ["unvalued_asset:spot:UNKNOWN"])


class QueuedCredentialBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_queue_injects_credentials_only_into_provider_payload_not_result(self):
        class CapturingProvider:
            provider_name = "ccxt"
            payload = None

            async def refresh(self, organization_id, integration, payload=None):
                self.payload = dict(payload or {})
                return ProviderRefreshResult(
                    status="ok",
                    data={
                        "balance": {
                            "integration_id": integration.id,
                            "service": "binance",
                            "accounts": [],
                            "assets": [],
                            "total_usd": 0.0,
                            "actual": True,
                        }
                    },
                )

        async with self.session_maker() as session:
            org = Organization(name="Queue credentials", slug="queue-credentials")
            session.add(org)
            await session.flush()
            integration = Integration(
                organization_id=org.id,
                provider="ccxt",
                name="Binance",
                kind="cex",
                exchange_code="binance",
                account_ref="main",
                is_active=True,
            )
            session.add(integration)
            await session.flush()
            session.add_all(
                [
                    IntegrationSecret(
                        integration_id=integration.id,
                        key_name="api_key",
                        secret_value="queue-key",
                    ),
                    IntegrationSecret(
                        integration_id=integration.id,
                        key_name="api_secret",
                        secret_value="queue-secret",
                    ),
                    IntegrationSecret(
                        integration_id=integration.id,
                        key_name="api_password",
                        secret_value="queue-password",
                    ),
                ]
            )
            job = SyncJob(
                organization_id=org.id,
                integration_id=integration.id,
                job_type="refresh",
                status="queued",
                payload={"exchange_id": "binance"},
                result={},
            )
            session.add(job)
            await session.commit()

            provider = CapturingProvider()
            with patch("app.services.sync_job_service.get_provider", return_value=provider):
                updated = await SyncJobService(session).run_job(job.id)

        self.assertEqual(updated.status, "completed")
        self.assertEqual(
            provider.payload,
            {
                "exchange_id": "binance",
                "api_key": "queue-key",
                "api_secret": "queue-secret",
                "api_password": "queue-password",
            },
        )
        serialized_result = repr(updated.result)
        for value in ("queue-key", "queue-secret", "queue-password"):
            self.assertNotIn(value, serialized_result)


class CCXTAggregationContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_aggregation_preserves_every_source_label_and_counts_each_once(self):
        manager = CCXTManager()
        account_types = EXCHANGE_ACCOUNT_TYPES["binance"]
        expected_total = sum(range(1, len(account_types) + 1))
        account_results = [
            AccountBalanceSchema(
                account_type=mapping["type"],
                assets=[AssetSchema(coin="USDT", amount=index, value_usd=float(index))],
                total_usd=float(index),
            )
            for index, mapping in enumerate(account_types, start=1)
        ]
        exchange = AsyncMock()
        exchange.close = AsyncMock()
        with (
            patch.object(manager, "_get_exchange", new=AsyncMock(return_value=exchange)),
            patch.object(manager, "_get_tickers", new=AsyncMock(return_value={})),
            patch.object(
                manager,
                "_fetch_account_balance",
                new=AsyncMock(side_effect=account_results),
            ),
            patch("app.services.ccxt_manager.settings.ccxt_inter_account_delay_seconds", 0.0),
        ):
            result = await manager._fetch_balance_via_ccxt("binance")

        self.assertEqual([account.account_type for account in result.accounts], EXPECTED_ACCOUNT_REGISTRY["binance"])
        self.assertEqual(result.total_usd, float(expected_total))
        self.assertEqual(result.assets[0].amount, float(expected_total))
        self.assertEqual(result.assets[0].value_usd, float(expected_total))

    async def test_gateio_mirrored_view_is_visible_but_not_double_counted(self):
        manager = CCXTManager()
        exchange = AsyncMock()
        exchange.close = AsyncMock()
        results = [
            AccountBalanceSchema(
                account_type="spot",
                assets=[AssetSchema(coin="USDT", amount=100.0, value_usd=100.0)],
                total_usd=100.0,
            ),
            AccountBalanceSchema(
                account_type="margin",
                assets=[],
                total_usd=0.0,
            ),
            AccountBalanceSchema(
                account_type="usdt_futures",
                assets=[AssetSchema(coin="USDT", amount=100.0, value_usd=100.0)],
                total_usd=100.0,
            ),
        ]
        with (
            patch.object(manager, "_get_exchange", new=AsyncMock(return_value=exchange)),
            patch.object(manager, "_get_tickers", new=AsyncMock(return_value={})),
            patch.object(manager, "_fetch_account_balance", new=AsyncMock(side_effect=results)),
            patch("app.services.ccxt_manager.settings.ccxt_inter_account_delay_seconds", 0.0),
        ):
            result = await manager._fetch_balance_via_ccxt("gateio")

        self.assertEqual([account.account_type for account in result.accounts], ["spot", "usdt_futures"])
        self.assertEqual(result.accounts[1].mirror_of, "spot")
        self.assertEqual(result.total_usd, 100.0)
        self.assertEqual(result.assets[0].amount, 100.0)


class MirrorHistoryContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_mirror_transition_creates_history_row(self):
        from app.repositories.balance import BalanceRepository

        account = AccountBalanceSchema(
            account_type="usdt_futures",
            assets=[AssetSchema(coin="USDT", amount=100.0, value_usd=100.0)],
            total_usd=100.0,
        )
        async with self.session_maker() as session:
            repo = BalanceRepository(session)
            await repo.save_balance(
                service="gateio",
                assets=[AssetSchema(coin="USDT", amount=100.0, value_usd=100.0)],
                total_usd=100.0,
                accounts=[account],
                organization_id=1,
                integration_id=1,
            )
            await repo.save_balance(
                service="gateio",
                assets=[AssetSchema(coin="USDT", amount=100.0, value_usd=100.0)],
                total_usd=100.0,
                accounts=[account.model_copy(update={"mirror_of": "spot"})],
                organization_id=1,
                integration_id=1,
            )
            history = await repo.get_history(
                organization_id=1,
                service="gateio",
                integration_id=1,
            )

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].accounts[0]["mirror_of"], "spot")
