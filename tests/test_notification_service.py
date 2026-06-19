import unittest
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.balance import (
    Organization,
    OrganizationMembership,
    ServiceStatus,
    SyncJob,
    Transaction,
    User,
)
from app.schemas.notifications import NotificationSettingsUpdate
from app.services.notification_service import NotificationService


class NotificationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _org_user(self, session):
        org = Organization(name="Org", slug="org")
        user = User(
            email="user@example.com",
            password_hash="x",
            is_active=True,
            telegram_user_id=123,
        )
        session.add_all([org, user])
        await session.flush()
        session.add(
            OrganizationMembership(
                organization_id=org.id,
                user_id=user.id,
                role="owner",
            )
        )
        await session.commit()
        return org, user

    async def test_transaction_enable_seeds_baseline_and_dedupes_new_events(self):
        async with self.session_maker() as session:
            org, user = await self._org_user(session)
            session.add(
                ServiceStatus(
                    organization_id=org.id,
                    service="bingx",
                    is_healthy=True,
                )
            )
            session.add(
                Transaction(
                    organization_id=org.id,
                    tx_id="old",
                    service="bingx",
                    tx_type="deposit",
                    currency="USDT",
                    amount=1,
                    status="ok",
                    tx_timestamp=datetime.now(timezone.utc),
                )
            )
            await session.commit()

            service = NotificationService(session)
            settings = await service.update_settings(
                organization_id=org.id,
                user_id=user.id,
                payload=NotificationSettingsUpdate(transaction_enabled=True),
            )
            self.assertTrue(settings.transaction_enabled)
            self.assertEqual(
                await service.generate_transaction_events(organization_id=org.id),
                0,
            )

            session.add(
                Transaction(
                    organization_id=org.id,
                    tx_id="new",
                    service="bingx",
                    tx_type="withdrawal",
                    currency="USDT",
                    amount=2,
                    status="ok",
                    tx_timestamp=datetime.now(timezone.utc),
                )
            )
            await session.commit()

            self.assertEqual(
                await service.generate_transaction_events(organization_id=org.id),
                1,
            )
            self.assertEqual(
                await service.generate_transaction_events(organization_id=org.id),
                0,
            )

    async def test_transaction_events_skip_unhealthy_or_unsupported_sources(self):
        async with self.session_maker() as session:
            org, user = await self._org_user(session)
            session.add_all(
                [
                    ServiceStatus(
                        organization_id=org.id,
                        service="bingx",
                        is_healthy=False,
                    ),
                    ServiceStatus(
                        organization_id=org.id,
                        service="lbank",
                        is_healthy=True,
                    ),
                ]
            )
            await session.commit()

            service = NotificationService(session)
            await service.update_settings(
                organization_id=org.id,
                user_id=user.id,
                payload=NotificationSettingsUpdate(transaction_enabled=True),
            )
            session.add_all(
                [
                    Transaction(
                        organization_id=org.id,
                        tx_id="unhealthy",
                        service="bingx",
                        tx_type="deposit",
                        currency="USDT",
                        amount=1,
                        status="ok",
                        tx_timestamp=datetime.now(timezone.utc),
                    ),
                    Transaction(
                        organization_id=org.id,
                        tx_id="unsupported",
                        service="lbank",
                        tx_type="deposit",
                        currency="USDT",
                        amount=1,
                        status="ok",
                        tx_timestamp=datetime.now(timezone.utc),
                    ),
                ]
            )
            await session.commit()

            self.assertEqual(
                await service.generate_transaction_events(organization_id=org.id),
                0,
            )

    async def test_transaction_events_include_healthy_dex_wallet_sources(self):
        async with self.session_maker() as session:
            org, user = await self._org_user(session)
            service_key = "evm_0xabc"
            session.add(
                ServiceStatus(
                    organization_id=org.id,
                    service=service_key,
                    is_healthy=True,
                )
            )
            await session.commit()

            service = NotificationService(session)
            await service.update_settings(
                organization_id=org.id,
                user_id=user.id,
                payload=NotificationSettingsUpdate(transaction_enabled=True),
            )
            session.add(
                Transaction(
                    organization_id=org.id,
                    tx_id="dex-new",
                    service=service_key,
                    tx_type="deposit",
                    currency="USDC",
                    amount=3,
                    status="ok",
                    network="eth",
                    tx_timestamp=datetime.now(timezone.utc),
                )
            )
            await session.commit()

            self.assertEqual(
                await service.generate_transaction_events(organization_id=org.id),
                1,
            )
            events = await service.list_events(organization_id=org.id, user_id=user.id)
            self.assertEqual(events.events[0].event_type, "transaction")
            self.assertIn("DEX", events.events[0].title)

    async def test_system_status_events_baseline_then_transition(self):
        async with self.session_maker() as session:
            org, user = await self._org_user(session)
            status = ServiceStatus(
                organization_id=org.id,
                service="okx",
                is_healthy=True,
                last_error=None,
            )
            session.add(status)
            await session.commit()

            service = NotificationService(session)
            self.assertEqual(
                await service.generate_system_status_events(organization_id=org.id),
                0,
            )
            settings = await service.get_settings(organization_id=org.id, user_id=user.id)
            self.assertTrue(settings.system_enabled)

            status.is_healthy = False
            status.last_error = "401 Unauthorized"
            status.last_check = datetime.now(timezone.utc)
            await session.commit()

            self.assertEqual(
                await service.generate_system_status_events(organization_id=org.id),
                1,
            )
            self.assertEqual(
                await service.generate_system_status_events(organization_id=org.id),
                0,
            )

            events = await service.list_events(organization_id=org.id, user_id=user.id)
            self.assertEqual(events.total_count, 1)
            self.assertEqual(events.events[0].event_type, "service_unhealthy")
            self.assertIn("401", events.events[0].body)

    async def test_sync_job_events_baseline_then_timeout_failure(self):
        async with self.session_maker() as session:
            org, user = await self._org_user(session)
            old_job = SyncJob(
                organization_id=org.id,
                integration_id=None,
                job_type="refresh",
                status="failed",
                error_message="old timeout",
                result={"code": "refresh_provider_timeout"},
            )
            session.add(old_job)
            await session.commit()

            service = NotificationService(session)
            self.assertEqual(
                await service.generate_sync_job_events(organization_id=org.id),
                0,
            )

            session.add(
                SyncJob(
                    organization_id=org.id,
                    integration_id=None,
                    job_type="refresh",
                    status="failed",
                    error_message="Refresh provider timeout after 120.0s",
                    result={"code": "refresh_provider_timeout"},
                )
            )
            await session.commit()

            self.assertEqual(
                await service.generate_sync_job_events(organization_id=org.id),
                1,
            )
            self.assertEqual(
                await service.generate_sync_job_events(organization_id=org.id),
                0,
            )
            events = await service.list_events(organization_id=org.id, user_id=user.id)
            self.assertEqual(events.total_count, 1)
            self.assertEqual(events.events[0].event_type, "sync_job_failed")


if __name__ == "__main__":
    unittest.main()
