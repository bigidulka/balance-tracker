import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.balance import Integration, Organization, SyncJob
from app.repositories.balance import BalanceRepository, ServiceStatusRepository
from app.schemas.balance import AccountBalanceSchema, AssetSchema, ServiceBalanceSchema
from app.services.balance_integrity import BalanceIntegrityError, validate_balance_shape
from app.services.balance_service import BalanceService
from app.services.sync_job_service import SyncJobService


class BalanceIntegrityHotfixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_save_balance_persists_positive_to_zero_transition(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)

            await repo.save_balance(
                service="binance",
                assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                total_usd=100,
                actual=True,
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                        total_usd=100,
                    )
                ],
                organization_id=1,
            )

            latest = await repo.save_balance(
                service="binance",
                assets=[],
                total_usd=0,
                actual=True,
                accounts=[],
                organization_id=1,
            )

            history = await repo.get_history(organization_id=1, service="binance", limit=10)

        self.assertEqual(latest.total_usd, 0)
        self.assertTrue(latest.actual)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].total_usd, 0)

    async def test_save_balance_tracks_accounts_change_even_without_total_change(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)
            await repo.save_balance(
                service="binance",
                assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                total_usd=100,
                actual=True,
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                        total_usd=100,
                    )
                ],
                organization_id=1,
            )

            await repo.save_balance(
                service="binance",
                assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                total_usd=100,
                actual=True,
                accounts=[
                    AccountBalanceSchema(
                        account_type="futures",
                        assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                        total_usd=100,
                    )
                ],
                organization_id=1,
            )

            history = await repo.get_history(organization_id=1, service="binance", limit=10)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].accounts[0]["account_type"], "futures")

    async def test_balance_service_fallback_returns_known_zero_balance(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)
            await repo.save_balance(
                service="binance",
                assets=[],
                total_usd=0,
                actual=True,
                accounts=[],
                organization_id=1,
            )

            service = BalanceService(session=session, organization_id=1)
            fallback = await service._get_fallback_balance("binance")

        self.assertIsNotNone(fallback)
        self.assertEqual(fallback.total_usd, 0)
        self.assertFalse(fallback.actual)

    async def test_balance_shape_rejects_total_assets_mismatch(self):
        balance = ServiceBalanceSchema(
            service="binance",
            assets=[AssetSchema(coin="USDC_base", amount=10, value_usd=10)],
            accounts=[],
            total_usd=1000000,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

        with self.assertRaisesRegex(BalanceIntegrityError, "total/assets mismatch"):
            validate_balance_shape(balance)

    async def test_wallet_shape_allows_portfolio_total_assets_mismatch(self):
        balance = ServiceBalanceSchema(
            service="evm_0xabc",
            assets=[AssetSchema(coin="USDC_base", amount=10, value_usd=10)],
            accounts=[],
            total_usd=6653.64,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

        validate_balance_shape(balance)

    async def test_wallet_shape_allows_empty_positive_portfolio_total(self):
        balance = ServiceBalanceSchema(
            service="debank_sdk_0xabc",
            assets=[],
            accounts=[],
            total_usd=6653.64,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

        validate_balance_shape(balance)

    async def test_balance_service_rejects_outlier_and_keeps_previous(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)
            await repo.save_balance(
                service="evm_0xabc",
                assets=[AssetSchema(coin="USDC", amount=1000, value_usd=1000)],
                total_usd=1000,
                actual=True,
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDC", amount=1000, value_usd=1000)],
                        total_usd=1000,
                    )
                ],
                organization_id=1,
                integration_id=1,
            )

            service = BalanceService(session=session, organization_id=1)
            outlier = ServiceBalanceSchema(
                service="evm_0xabc",
                assets=[AssetSchema(coin="USDC", amount=10000, value_usd=10000)],
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDC", amount=10000, value_usd=10000)],
                        total_usd=10000,
                    )
                ],
                total_usd=10000,
                updated_at=datetime.now(timezone.utc),
                actual=True,
            )

            with self.assertRaisesRegex(BalanceIntegrityError, "outlier"):
                await service.fetch_and_save_balance("evm_0xabc", outlier, integration_id=1)

            latest = await repo.get_latest_balance("evm_0xabc", organization_id=1, integration_id=1)
            history = await repo.get_history(
                organization_id=1,
                service="evm_0xabc",
                integration_id=1,
                limit=10,
            )

        self.assertEqual(latest.total_usd, 1000)
        self.assertEqual(len(history), 1)

    async def test_wallet_outlier_accepts_value_with_recent_history_support(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)
            for total in [6651.3, 6651.31, 6651.32, 1500.0]:
                await repo.save_balance(
                    service="evm_0xabc",
                    assets=[AssetSchema(coin="USDC", amount=1, value_usd=1)],
                    total_usd=total,
                    actual=True,
                    accounts=[
                        AccountBalanceSchema(
                            account_type="spot",
                            assets=[AssetSchema(coin="USDC", amount=1, value_usd=1)],
                            total_usd=total,
                        )
                    ],
                    organization_id=1,
                    integration_id=1,
                )

            service = BalanceService(session=session, organization_id=1)
            restored = ServiceBalanceSchema(
                service="evm_0xabc",
                assets=[AssetSchema(coin="USDC", amount=1, value_usd=1)],
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDC", amount=1, value_usd=1)],
                        total_usd=6651.30,
                    )
                ],
                total_usd=6651.30,
                updated_at=datetime.now(timezone.utc),
                actual=True,
            )
            saved = await service.fetch_and_save_balance(
                "evm_0xabc",
                restored,
                integration_id=1,
            )

        self.assertAlmostEqual(saved.total_usd, 6651.30, places=2)

    async def test_wallet_large_drop_without_history_support_rejected(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)
            await repo.save_balance(
                service="evm_0xabc",
                assets=[AssetSchema(coin="USDC", amount=1, value_usd=1)],
                total_usd=6651.30,
                actual=True,
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDC", amount=1, value_usd=1)],
                        total_usd=6651.30,
                    )
                ],
                organization_id=1,
                integration_id=1,
            )

            service = BalanceService(session=session, organization_id=1)
            low = ServiceBalanceSchema(
                service="evm_0xabc",
                assets=[AssetSchema(coin="USDC", amount=1, value_usd=1)],
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDC", amount=1, value_usd=1)],
                        total_usd=25.0,
                    )
                ],
                total_usd=25.0,
                updated_at=datetime.now(timezone.utc),
                actual=True,
            )

            with self.assertRaisesRegex(BalanceIntegrityError, "outlier"):
                await service.fetch_and_save_balance("evm_0xabc", low, integration_id=1)

    async def test_balance_service_rejects_outlier_against_stale_previous(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)
            await repo.save_balance(
                service="evm_0xabc",
                assets=[AssetSchema(coin="USDC", amount=1000, value_usd=1000)],
                total_usd=1000,
                actual=True,
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDC", amount=1000, value_usd=1000)],
                        total_usd=1000,
                    )
                ],
                organization_id=1,
                integration_id=1,
            )
            await repo.mark_as_stale("evm_0xabc", organization_id=1, integration_id=1)

            service = BalanceService(session=session, organization_id=1)
            outlier = ServiceBalanceSchema(
                service="evm_0xabc",
                assets=[AssetSchema(coin="USDC", amount=10000, value_usd=10000)],
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDC", amount=10000, value_usd=10000)],
                        total_usd=10000,
                    )
                ],
                total_usd=10000,
                updated_at=datetime.now(timezone.utc),
                actual=True,
            )

            with self.assertRaisesRegex(BalanceIntegrityError, "outlier"):
                await service.fetch_and_save_balance("evm_0xabc", outlier, integration_id=1)

            latest = await repo.get_latest_balance("evm_0xabc", organization_id=1, integration_id=1)

        self.assertEqual(latest.total_usd, 1000)
        self.assertFalse(latest.actual)

    async def test_balance_service_allows_first_empty_zero_balance(self):
        async with self.session_maker() as session:
            service = BalanceService(session=session, organization_id=1)
            zero = ServiceBalanceSchema(
                service="new_empty_exchange",
                assets=[],
                accounts=[],
                total_usd=0,
                updated_at=datetime.now(timezone.utc),
                actual=True,
            )

            saved = await service.fetch_and_save_balance("new_empty_exchange", zero)

        self.assertEqual(saved.total_usd, 0)
        self.assertTrue(saved.actual)

    async def test_balance_service_rejects_empty_positive_payload(self):
        async with self.session_maker() as session:
            service = BalanceService(session=session, organization_id=1)
            empty_positive = ServiceBalanceSchema(
                service="binance",
                assets=[],
                accounts=[],
                total_usd=25,
                updated_at=datetime.now(timezone.utc),
                actual=True,
            )

            with self.assertRaisesRegex(BalanceIntegrityError, "Empty positive"):
                await service.fetch_and_save_balance("binance", empty_positive)

    async def test_sync_job_provider_exception_marks_cex_unhealthy(self):
        class FailingProvider:
            async def refresh(self, organization_id, integration, payload=None):
                raise RuntimeError("401, message='Unauthorized'")

        async with self.session_maker() as session:
            org = Organization(name="Org", slug="org")
            session.add(org)
            await session.flush()
            integration = Integration(
                organization_id=org.id,
                provider="ccxt",
                name="OKX",
                kind="cex",
                exchange_code="okx",
                account_ref="main",
                is_active=True,
            )
            session.add(integration)
            await session.flush()
            job = SyncJob(
                organization_id=org.id,
                integration_id=integration.id,
                job_type="refresh",
                status="queued",
                payload={},
                result={},
            )
            session.add(job)
            await session.commit()

            with patch("app.services.sync_job_service.get_provider", return_value=FailingProvider()):
                updated = await SyncJobService(session).run_job(job.id)

            status = await ServiceStatusRepository(session).get_status("okx", org.id)

        self.assertEqual(updated.status, "failed")
        self.assertIsNotNone(status)
        self.assertFalse(status.is_healthy)
        self.assertIn("Unauthorized", status.last_error)

    async def test_sync_job_provider_timeout_marks_cex_unhealthy(self):
        class SlowProvider:
            async def refresh(self, organization_id, integration, payload=None):
                await asyncio.sleep(10)

        async with self.session_maker() as session:
            org = Organization(name="Org Timeout", slug="org-timeout")
            session.add(org)
            await session.flush()
            integration = Integration(
                organization_id=org.id,
                provider="ccxt",
                name="Gate",
                kind="cex",
                exchange_code="gateio",
                account_ref="main",
                is_active=True,
            )
            session.add(integration)
            await session.flush()
            job = SyncJob(
                organization_id=org.id,
                integration_id=integration.id,
                job_type="refresh",
                status="queued",
                payload={},
                result={},
            )
            session.add(job)
            await session.commit()

            with (
                patch("app.services.sync_job_service.get_provider", return_value=SlowProvider()),
                patch("app.services.sync_job_service.settings.sync_job_provider_timeout_seconds", 0.01),
            ):
                updated = await SyncJobService(session).run_job(job.id)

            status = await ServiceStatusRepository(session).get_status("gateio", org.id)

        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.result["code"], "refresh_provider_timeout")
        self.assertIn("timeout", updated.error_message.lower())
        self.assertIsNotNone(status)
        self.assertFalse(status.is_healthy)
        self.assertIn("timeout", status.last_error.lower())

    async def test_sync_job_provider_failed_result_marks_cex_unhealthy(self):
        from app.services.integrations.provider import ProviderRefreshResult

        class FailedProvider:
            async def refresh(self, organization_id, integration, payload=None):
                return ProviderRefreshResult(status="failed", message="403 Forbidden")

        async with self.session_maker() as session:
            org = Organization(name="Org 2", slug="org-2")
            session.add(org)
            await session.flush()
            integration = Integration(
                organization_id=org.id,
                provider="ccxt",
                name="BitMart",
                kind="cex",
                exchange_code="bitmart",
                account_ref="main",
                is_active=True,
            )
            session.add(integration)
            await session.flush()
            job = SyncJob(
                organization_id=org.id,
                integration_id=integration.id,
                job_type="refresh",
                status="queued",
                payload={},
                result={},
            )
            session.add(job)
            await session.commit()

            with patch("app.services.sync_job_service.get_provider", return_value=FailedProvider()):
                updated = await SyncJobService(session).run_job(job.id)

            status = await ServiceStatusRepository(session).get_status("bitmart", org.id)

        self.assertEqual(updated.status, "failed")
        self.assertIsNotNone(status)
        self.assertFalse(status.is_healthy)
        self.assertEqual(status.last_error, "403 Forbidden")

    async def test_sync_job_persist_rejects_invalid_balance_and_marks_unhealthy(self):
        async with self.session_maker() as session:
            service = SyncJobService(session)
            with self.assertRaisesRegex(BalanceIntegrityError, "total/assets mismatch"):
                await service._persist_balance_from_refresh_result(
                    1,
                    {
                        "balance": {
                            "integration_id": 10,
                            "service": "binance",
                            "assets": [
                                {"coin": "USDC", "amount": 10, "value_usd": 10}
                            ],
                            "accounts": [],
                            "total_usd": 1000000,
                            "actual": True,
                        }
                    },
                )

            latest = await BalanceRepository(session).get_latest_balance(
                "binance",
                organization_id=1,
                integration_id=10,
            )
            status = await ServiceStatusRepository(session).get_status(
                "binance",
                organization_id=1,
            )

        self.assertIsNone(latest)
        self.assertIsNotNone(status)
        self.assertFalse(status.is_healthy)
        self.assertIn("total/assets mismatch", status.last_error)
