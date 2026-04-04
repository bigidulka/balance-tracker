import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.balance import Integration, Organization, Plan, Subscription, SyncJob
from app.schemas.balance import ServiceBalanceSchema
from app.schemas.integration import IntegrationCreateRequest
from app.services.balance_service import BalanceService
from app.services.entitlements_service import EntitlementsService
from app.services.integration_service import IntegrationService
from app.services.refresh_orchestrator import RefreshOrchestrator
from app.services.sync_job_service import SyncJobService
import app.services.sync_job_service as sync_job_service_module


class PolicyResolutionTests(unittest.TestCase):
    def test_json_policy_resolution_for_free_and_pro_plans(self):
        free = Plan(
            code="free",
            name="Free",
            max_integrations=6,
            min_refresh_interval_seconds=600,
            policy_json={
                "version": 1,
                "features": {"allow_dex": True},
                "limits": {
                    "max_integrations": 6,
                    "max_accounts_per_exchange": 0,
                    "max_cex_accounts": 5,
                    "max_evm_wallets": 1,
                    "min_refresh_interval_seconds": 600,
                },
                "background": {
                    "enabled": True,
                    "refresh_interval_seconds": 600,
                },
                "throttling": {
                    "min_refresh_interval_seconds": 600,
                    "background_refresh_interval_seconds": 600,
                },
            },
            is_active=True,
        )
        pro = Plan(
            code="pro",
            name="Pro",
            max_integrations=71,
            min_refresh_interval_seconds=60,
            policy_json={
                "version": 2,
                "features": {"allow_dex": True},
                "limits": {
                    "max_integrations": 71,
                    "max_accounts_per_exchange": 0,
                    "max_cex_accounts": 56,
                    "max_evm_wallets": 15,
                    "min_refresh_interval_seconds": 60,
                },
                "background": {
                    "enabled": True,
                    "refresh_interval_seconds": 60,
                },
                "throttling": {
                    "min_refresh_interval_seconds": 60,
                    "background_refresh_interval_seconds": 60,
                },
            },
            is_active=True,
        )

        free_policy = EntitlementsService._resolve_policy(free)
        pro_policy = EntitlementsService._resolve_policy(pro)

        self.assertEqual(free_policy["version"], 1)
        self.assertEqual(free_policy["features"]["allow_dex"], True)
        self.assertEqual(free_policy["limits"]["max_integrations"], 6)
        self.assertEqual(free_policy["limits"]["max_cex_accounts"], 5)
        self.assertEqual(free_policy["limits"]["max_evm_wallets"], 1)
        self.assertEqual(free_policy["limits"]["max_accounts_per_exchange"], 0)
        self.assertEqual(free_policy["background"]["refresh_interval_seconds"], 600)
        self.assertEqual(free_policy["throttling"]["background_refresh_interval_seconds"], 600)

        self.assertEqual(pro_policy["version"], 2)
        self.assertEqual(pro_policy["features"]["allow_dex"], True)
        self.assertEqual(pro_policy["limits"]["max_integrations"], 71)
        self.assertEqual(pro_policy["limits"]["max_cex_accounts"], 56)
        self.assertEqual(pro_policy["limits"]["max_evm_wallets"], 15)
        self.assertEqual(pro_policy["limits"]["max_accounts_per_exchange"], 0)
        self.assertEqual(pro_policy["background"]["refresh_interval_seconds"], 60)
        self.assertEqual(pro_policy["throttling"]["background_refresh_interval_seconds"], 60)

    def test_legacy_policy_resolution_remains_supported(self):
        plan = Plan(
            code="legacy",
            name="Legacy",
            max_integrations=7,
            min_refresh_interval_seconds=120,
            policy_json={
                "limits": {"max_accounts_per_exchange": 4},
                "throttling": {"min_refresh_interval_seconds": 90},
                "capabilities": {"allow_dex": 1},
            },
            is_active=True,
        )

        policy = EntitlementsService._resolve_policy(plan)

        self.assertEqual(policy["version"], 1)
        self.assertEqual(policy["features"]["allow_dex"], True)
        self.assertEqual(policy["limits"]["max_integrations"], 7)
        self.assertEqual(policy["limits"]["max_cex_accounts"], 7)
        self.assertEqual(policy["limits"]["max_evm_wallets"], 0)
        self.assertEqual(policy["limits"]["max_accounts_per_exchange"], 4)
        self.assertEqual(policy["background"]["refresh_interval_seconds"], 90)
        self.assertEqual(policy["throttling"]["background_refresh_interval_seconds"], 90)


class EntitlementEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_free_org_with_plan(self):
        async with self.session_maker() as session:
            org = Organization(name="Org One", slug="org-one")
            session.add(org)
            await session.flush()

            plan = Plan(
                code="free",
                name="Free",
                max_integrations=6,
                min_refresh_interval_seconds=600,
                policy_json={
                    "version": 1,
                    "features": {"allow_dex": True},
                    "limits": {
                        "max_integrations": 6,
                        "max_accounts_per_exchange": 0,
                        "max_cex_accounts": 5,
                        "max_evm_wallets": 1,
                        "min_refresh_interval_seconds": 600,
                    },
                    "background": {
                        "enabled": True,
                        "refresh_interval_seconds": 600,
                    },
                    "throttling": {
                        "min_refresh_interval_seconds": 600,
                        "background_refresh_interval_seconds": 600,
                    },
                },
                is_active=True,
            )
            session.add(plan)
            await session.flush()

            sub = Subscription(
                organization_id=org.id,
                plan_id=plan.id,
                status="active",
            )
            session.add(sub)
            await session.commit()
            return org.id

    async def test_free_plan_limits_cex_accounts(self):
        org_id = await self._seed_free_org_with_plan()

        async with self.session_maker() as session:
            for index in range(5):
                session.add(
                    Integration(
                        organization_id=org_id,
                        provider="binance",
                        name=f"Binance {index}",
                        kind="cex",
                        exchange_code="binance" if index < 3 else "bybit",
                        account_ref=f"acc-{index}",
                        is_active=True,
                    )
                )
            await session.commit()

            service = EntitlementsService(session)
            with self.assertRaises(HTTPException) as exc:
                await service.ensure_can_create_integration(
                    org_id,
                    kind="cex",
                    exchange_code="okx",
                )

            self.assertEqual(exc.exception.status_code, 403)
            self.assertEqual(exc.exception.detail["code"], "cex_account_limit_reached")
            self.assertEqual(exc.exception.detail["policy"]["max_cex_accounts"], 5)

    async def test_free_plan_limits_evm_wallets_but_not_non_evm_wallets(self):
        org_id = await self._seed_free_org_with_plan()

        async with self.session_maker() as session:
            session.add(
                Integration(
                    organization_id=org_id,
                    provider="debank",
                    name="EVM Wallet",
                    kind="dex",
                    wallet_address="0x111",
                    chain="ethereum",
                    is_active=True,
                )
            )
            await session.commit()

            service = EntitlementsService(session)
            with self.assertRaises(HTTPException) as exc:
                await service.ensure_can_create_integration(
                    org_id,
                    kind="dex",
                    chain="base",
                )

            self.assertEqual(exc.exception.status_code, 403)
            self.assertEqual(exc.exception.detail["code"], "evm_wallet_limit_reached")
            self.assertEqual(exc.exception.detail["policy"]["max_evm_wallets"], 1)

            await service.ensure_can_create_integration(
                org_id,
                kind="dex",
                chain="solana",
            )


class EffectiveEntitlementsDatetimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_get_effective_entitlements_handles_naive_last_refresh(self):
        async with self.session_maker() as session:
            org = Organization(name="Org Three", slug="org-three")
            session.add(org)
            await session.flush()

            plan = Plan(
                code="free",
                name="Free",
                max_integrations=6,
                min_refresh_interval_seconds=600,
                policy_json={
                    "version": 1,
                    "features": {"allow_dex": True},
                    "limits": {
                        "max_integrations": 6,
                        "max_accounts_per_exchange": 0,
                        "max_cex_accounts": 5,
                        "max_evm_wallets": 1,
                        "min_refresh_interval_seconds": 600,
                    },
                    "background": {
                        "enabled": True,
                        "refresh_interval_seconds": 600,
                    },
                    "throttling": {
                        "min_refresh_interval_seconds": 600,
                        "background_refresh_interval_seconds": 600,
                    },
                },
                is_active=True,
            )
            session.add(plan)
            await session.flush()

            session.add(Subscription(organization_id=org.id, plan_id=plan.id, status="active"))
            session.add(
                Integration(
                    organization_id=org.id,
                    provider="binance",
                    name="Binance CEX",
                    kind="cex",
                    exchange_code="binance",
                    account_ref="main",
                    is_active=True,
                )
            )
            session.add(
                Integration(
                    organization_id=org.id,
                    provider="debank",
                    name="EVM Wallet",
                    kind="dex",
                    wallet_address="0xabc",
                    chain="ethereum",
                    is_active=True,
                )
            )
            session.add(
                Integration(
                    organization_id=org.id,
                    provider="okx_wallet",
                    name="Solana Wallet",
                    kind="dex",
                    wallet_address="So11111111111111111111111111111111111111112",
                    chain="solana",
                    is_active=True,
                )
            )
            session.add(
                SyncJob(
                    organization_id=org.id,
                    job_type="refresh",
                    status="success",
                    payload={},
                    result={},
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            await session.commit()

            payload = await EntitlementsService(session).get_effective_entitlements(org.id)
            self.assertIn("last_refresh_at", payload)
            self.assertIn("policy", payload)
            self.assertEqual(payload["policy"]["version"], 1)
            self.assertIn("state", payload["policy"])
            self.assertIn("refresh", payload["policy"]["state"])
            self.assertIn("cex", payload["policy"]["state"])
            self.assertIn("evm", payload["policy"]["state"])
            self.assertIn("throttling", payload)
            self.assertIn("background", payload)
            self.assertIn("usage", payload)
            self.assertEqual(payload["throttling"]["min_refresh_interval_seconds"], 600)
            self.assertEqual(payload["throttling"]["background_refresh_interval_seconds"], 600)
            self.assertEqual(payload["background"]["refresh_interval_seconds"], 600)
            self.assertEqual(payload["usage"]["cex"]["active"], 1)
            self.assertEqual(payload["usage"]["evm"]["active"], 1)
            self.assertEqual(payload["usage"]["non_evm_dex"]["active"], 1)
            self.assertEqual(payload["limits"]["max_cex_accounts"], 5)
            self.assertEqual(payload["limits"]["max_evm_wallets"], 1)
            self.assertIsInstance(payload["capabilities"], dict)
            self.assertTrue(payload["capabilities"]["refresh"] in {True, False})
            self.assertIsInstance(payload["policy"]["state"]["integrations"], dict)


class RefreshRateLimitMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_sync_job_captures_retry_metadata_when_rate_limited(self):
        async with self.session_maker() as session:
            org = Organization(name="Org Two", slug="org-two")
            session.add(org)
            await session.flush()

            integration = Integration(
                organization_id=org.id,
                provider="binance",
                name="Binance A",
                kind="cex",
                exchange_code="binance",
                account_ref="sub-1",
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

            service = SyncJobService(session)
            service.entitlements.ensure_refresh_interval_for_organization = AsyncMock(
                side_effect=HTTPException(
                    status_code=429,
                    detail={
                        "code": "refresh_rate_limited",
                        "message": "Refresh rate limit exceeded for current plan",
                        "min_refresh_interval_seconds": 600,
                        "retry_after_seconds": 42,
                    },
                    headers={"Retry-After": "42"},
                )
            )

            updated_job = await service.run_job(job.id)

            self.assertEqual(updated_job.status, "failed")
            self.assertEqual(updated_job.result["status"], "rate_limited")
            self.assertEqual(updated_job.result["code"], "refresh_rate_limited")
            self.assertEqual(updated_job.result["retry_after_seconds"], 42)
            self.assertEqual(updated_job.result["min_refresh_interval_seconds"], 600)


class RefreshAllEntitlementSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _create_org(self, slug: str) -> int:
        async with self.session_maker() as session:
            org = Organization(name=slug, slug=slug)
            session.add(org)
            await session.commit()
            await session.refresh(org)
            return org.id

    async def test_all_failed_refresh_does_not_rate_limit_immediate_retry(self):
        org_id = await self._create_org("refresh-failed-org")

        async with self.session_maker() as session:
            service = BalanceService(session, organization_id=org_id)
            with (
                patch(
                    "app.services.balance_service.settings",
                    new=SimpleNamespace(
                        exchange_parallelism=1,
                    ),
                ),
                patch.object(
                    BalanceService,
                    "load_balance_targets",
                    new=AsyncMock(return_value=(["binance"], [], {"binance"})),
                ),
                patch.object(
                    service.entitlements,
                    "ensure_refresh_interval_for_organization",
                    wraps=service.entitlements.ensure_refresh_interval_for_organization,
                ) as ensure_refresh,
                patch(
                    "app.services.balance_service.ccxt_manager.fetch_all_balances",
                    new=AsyncMock(return_value={"binance": Exception("boom")}),
                ),
                patch(
                    "app.services.balance_service.okx_wallet_service.fetch_all_wallets",
                    new=AsyncMock(return_value={}),
                ),
            ):
                updated, failed = await service.refresh_all()
                self.assertEqual(updated, [])
                self.assertEqual(failed, ["binance"])

                updated_retry, failed_retry = await service.refresh_all()
                self.assertEqual(updated_retry, [])
                self.assertEqual(failed_retry, ["binance"])

                self.assertEqual(ensure_refresh.await_count, 2)

            jobs = (
                await session.execute(
                    select(SyncJob)
                    .where(SyncJob.organization_id == org_id)
                    .order_by(SyncJob.id.asc())
                )
            ).scalars().all()
            self.assertEqual([job.status for job in jobs], ["failed", "failed"])

    async def test_partial_refresh_still_counts_as_success_for_entitlements(self):
        org_id = await self._create_org("refresh-partial-org")

        async with self.session_maker() as session:
            service = BalanceService(session, organization_id=org_id)

            success_balance = ServiceBalanceSchema(
                service="binance",
                accounts=[],
                assets=[],
                total_usd=10.0,
                updated_at=datetime.now(timezone.utc),
                actual=True,
            )

            with (
                patch(
                    "app.services.balance_service.settings",
                    new=SimpleNamespace(
                        exchange_parallelism=1,
                    ),
                ),
                patch.object(
                    BalanceService,
                    "load_balance_targets",
                    new=AsyncMock(return_value=(["binance", "okx"], [], {"binance", "okx"})),
                ),
                patch(
                    "app.services.balance_service.ccxt_manager.fetch_all_balances",
                    new=AsyncMock(
                        return_value={
                            "binance": success_balance,
                            "okx": Exception("boom"),
                        }
                    ),
                ),
                patch(
                    "app.services.balance_service.okx_wallet_service.fetch_all_wallets",
                    new=AsyncMock(return_value={}),
                ),
            ):
                updated, failed = await service.refresh_all()
                self.assertEqual(updated, ["binance"])
                self.assertEqual(failed, ["okx"])

            jobs = (
                await session.execute(
                    select(SyncJob)
                    .where(SyncJob.organization_id == org_id)
                    .order_by(SyncJob.id.asc())
                )
            ).scalars().all()
            self.assertEqual([job.status for job in jobs], ["partial"])

            latest_success = await service.entitlements.get_latest_successful_refresh_at(org_id)
            self.assertIsNotNone(latest_success)


class SyncJobAtomicDedupeTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_identical_enqueue_produces_single_active_job(self):
        db_fd, db_path = tempfile.mkstemp(suffix="_syncjob_dedupe.sqlite3")
        try:
            engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
            session_maker = async_sessionmaker(engine, expire_on_commit=False)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            async with session_maker() as seed_session:
                org = Organization(name="dedupe-org", slug="dedupe-org")
                seed_session.add(org)
                await seed_session.flush()

                integration = Integration(
                    organization_id=org.id,
                    provider="binance",
                    name="Binance Dedupe",
                    kind="cex",
                    exchange_code="binance",
                    account_ref="main",
                    is_active=True,
                )
                seed_session.add(integration)
                await seed_session.commit()
                await seed_session.refresh(org)
                await seed_session.refresh(integration)
                organization_id = org.id
                integration_id = integration.id

            first_session = session_maker()
            second_session = session_maker()
            try:
                service_one = SyncJobService(first_session)
                service_two = SyncJobService(second_session)

                original_find_duplicate = SyncJobService._find_active_duplicate_job
                both_entered = asyncio.Event()
                state = {"entered": 0}

                async def synchronized_find_active_duplicate_job(self, dedupe_key: str):
                    state["entered"] += 1
                    if state["entered"] >= 2:
                        both_entered.set()
                    await both_entered.wait()
                    return await original_find_duplicate(self, dedupe_key)

                with patch.object(
                    sync_job_service_module.settings,
                    "enable_syncjob_dedupe",
                    True,
                ), patch.object(
                    SyncJobService,
                    "_find_active_duplicate_job",
                    new=synchronized_find_active_duplicate_job,
                ):
                    first_job, second_job = await asyncio.gather(
                        service_one.enqueue_job(
                            organization_id=organization_id,
                            integration_id=integration_id,
                            job_type="refresh",
                            payload={"scope": "full"},
                        ),
                        service_two.enqueue_job(
                            organization_id=organization_id,
                            integration_id=integration_id,
                            job_type="refresh",
                            payload={"scope": "full"},
                        ),
                    )

                self.assertEqual(first_job.id, second_job.id)

                async with session_maker() as check_session:
                    active_jobs_count = await check_session.scalar(
                        select(func.count())
                        .select_from(SyncJob)
                        .where(
                            SyncJob.organization_id == organization_id,
                            SyncJob.integration_id == integration_id,
                            SyncJob.job_type == "refresh",
                            SyncJob.status.in_(["queued", "running"]),
                        )
                    )
                    self.assertEqual(active_jobs_count, 1)
            finally:
                await first_session.close()
                await second_session.close()
                await engine.dispose()
        finally:
            os.close(db_fd)
            os.unlink(db_path)


class RefreshOrchestratorDelegationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_refresh_all_now_delegates_to_balance_service(self):
        async with self.session_maker() as session:
            orchestrator = RefreshOrchestrator(session)
            with patch(
                "app.services.refresh_orchestrator.BalanceService.refresh_all",
                new=AsyncMock(return_value=(["binance"], ["okx"])),
            ) as refresh_all:
                updated, failed = await orchestrator.refresh_all_now(organization_id=123)

            self.assertEqual(updated, ["binance"])
            self.assertEqual(failed, ["okx"])
            self.assertEqual(refresh_all.await_count, 1)

    async def test_queue_integration_refresh_delegates_to_sync_job_service(self):
        async with self.session_maker() as session:
            orchestrator = RefreshOrchestrator(session)
            expected_job = SimpleNamespace(id=77, status="queued")
            with patch(
                "app.services.refresh_orchestrator.SyncJobService.queue_refresh_for_integration",
                new=AsyncMock(return_value=expected_job),
            ) as queue_refresh:
                job = await orchestrator.queue_integration_refresh(
                    organization_id=11,
                    integration_id=22,
                    payload={"source": "manual_integration_refresh"},
                )

            self.assertEqual(job.id, 77)
            self.assertEqual(job.status, "queued")
            self.assertEqual(queue_refresh.await_count, 1)

    async def test_run_next_job_delegates_to_sync_job_service(self):
        async with self.session_maker() as session:
            orchestrator = RefreshOrchestrator(session)
            expected_job = SimpleNamespace(id=10, status="completed")
            with patch(
                "app.services.refresh_orchestrator.SyncJobService.run_next_job",
                new=AsyncMock(return_value=expected_job),
            ) as run_next:
                job = await orchestrator.run_next_job()

            self.assertEqual(job.id, 10)
            self.assertEqual(job.status, "completed")
            self.assertEqual(run_next.await_count, 1)


class IntegrationLifecycleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_activate_deactivate_and_refresh_queue_flow(self):
        async with self.session_maker() as session:
            org = Organization(name="Lifecycle Org", slug="lifecycle-org")
            session.add(org)
            await session.flush()

            integration = Integration(
                organization_id=org.id,
                provider="binance",
                name="Lifecycle Binance",
                kind="cex",
                exchange_code="binance",
                account_ref="main",
                is_active=True,
                status="active",
            )
            session.add(integration)
            await session.commit()
            await session.refresh(integration)

            service = IntegrationService(session)

            deactivated = await service.deactivate_integration(org.id, integration.id)
            self.assertFalse(deactivated.is_active)
            self.assertEqual(deactivated.status, "inactive")

            with self.assertRaises(HTTPException) as inactive_exc:
                await service.queue_integration_refresh(org.id, integration.id)
            self.assertEqual(inactive_exc.exception.status_code, 409)
            self.assertEqual(inactive_exc.exception.detail["code"], "integration_inactive")

            activated = await service.activate_integration(org.id, integration.id)
            self.assertTrue(activated.is_active)
            self.assertEqual(activated.status, "active")

            _, job_id, job_status = await service.queue_integration_refresh(org.id, integration.id)
            self.assertGreater(job_id, 0)
            self.assertIn(job_status, {"queued", "running"})

    async def test_activate_respects_plan_limits(self):
        org_id = await EntitlementEnforcementTests._seed_free_org_with_plan(self)

        async with self.session_maker() as session:
            for index in range(5):
                session.add(
                    Integration(
                        organization_id=org_id,
                        provider="ccxt",
                        name=f"CEX {index}",
                        kind="cex",
                        exchange_code="binance" if index < 4 else "bybit",
                        account_ref=f"acc-{index}",
                        is_active=True,
                        status="active",
                    )
                )
            extra = Integration(
                organization_id=org_id,
                provider="ccxt",
                name="Extra OKX",
                kind="cex",
                exchange_code="okx",
                account_ref="extra",
                is_active=False,
                status="inactive",
            )
            session.add(extra)
            await session.commit()
            await session.refresh(extra)

            service = IntegrationService(session)
            with self.assertRaises(HTTPException) as exc:
                await service.activate_integration(org_id, extra.id)

            self.assertEqual(exc.exception.status_code, 403)
            self.assertEqual(exc.exception.detail["code"], "cex_account_limit_reached")

    async def test_delete_integration_removes_record(self):
        async with self.session_maker() as session:
            org = Organization(name="Delete Org", slug="delete-org")
            session.add(org)
            await session.flush()

            integration = Integration(
                organization_id=org.id,
                provider="okx_wallet",
                name="Wallet",
                kind="dex",
                wallet_address="0xf9095877f93603d0b6c44e5a82db5dc751b34cd8",
                chain="ethereum",
                is_active=True,
                status="active",
            )
            session.add(integration)
            await session.commit()
            await session.refresh(integration)

            service = IntegrationService(session)
            await service.delete_integration(org.id, integration.id)

            with self.assertRaises(HTTPException) as exc:
                await service.get_integration(org.id, integration.id)
            self.assertEqual(exc.exception.status_code, 404)

    async def test_get_integration_enforces_tenant_scope(self):
        async with self.session_maker() as session:
            org1 = Organization(name="Org A", slug="org-a")
            org2 = Organization(name="Org B", slug="org-b")
            session.add_all([org1, org2])
            await session.flush()

            integration = Integration(
                organization_id=org1.id,
                provider="binance",
                name="Org A Binance",
                kind="cex",
                exchange_code="binance",
                account_ref="a-main",
                is_active=True,
            )
            session.add(integration)
            await session.commit()
            await session.refresh(integration)

            service = IntegrationService(session)
            with self.assertRaises(HTTPException) as exc:
                await service.get_integration(org2.id, integration.id)

            self.assertEqual(exc.exception.status_code, 404)
            self.assertEqual(exc.exception.detail["code"], "integration_not_found")


class IntegrationIdentityValidationTests(unittest.TestCase):
    def test_cex_identity_requires_exchange_and_account_ref(self):
        with self.assertRaises(ValidationError):
            IntegrationCreateRequest(provider="binance", name="Main", kind="cex")

    def test_dex_identity_requires_wallet_and_chain(self):
        with self.assertRaises(ValidationError):
            IntegrationCreateRequest(provider="debank", name="Wallet", kind="dex")

    def test_valid_cex_identity_is_accepted(self):
        payload = IntegrationCreateRequest(
            provider="binance",
            name="Main",
            kind="cex",
            exchange_code="binance",
            account_ref="sub-1",
        )
        self.assertEqual(payload.kind, "cex")
        self.assertEqual(payload.exchange_code, "binance")

    def test_valid_dex_identity_is_accepted(self):
        payload = IntegrationCreateRequest(
            provider="debank",
            name="Wallet",
            kind="dex",
            wallet_address="0x123",
            chain="eth",
        )
        self.assertEqual(payload.kind, "dex")
        self.assertEqual(payload.chain, "eth")
