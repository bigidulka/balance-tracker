import unittest
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.balance import Organization, OrganizationMembership, ServiceStatus, Transaction, User
from app.routers.notifications import (
    generate_notification_events,
    get_notification_settings,
    list_notification_events,
    mark_notification_event_sent,
    update_notification_settings,
)
from app.schemas.notifications import NotificationSettingsUpdate


class NotificationApiContractTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_settings_events_generate_and_mark_sent_contract(self):
        async with self.session_maker() as session:
            org, user = await self._org_user(session)

            settings = await get_notification_settings(
                db=session,
                organization_id=org.id,
                user=user,
                _=object(),
            )
            self.assertTrue(settings.enabled)
            self.assertTrue(settings.system_enabled)
            self.assertFalse(settings.transaction_enabled)

            updated = await update_notification_settings(
                payload=NotificationSettingsUpdate(transaction_enabled=True),
                db=session,
                organization_id=org.id,
                user=user,
                _=object(),
            )
            self.assertTrue(updated.transaction_enabled)

            session.add(
                ServiceStatus(
                    organization_id=org.id,
                    service="okx",
                    is_healthy=True,
                    last_error=None,
                )
            )
            await session.commit()
            first_generate = await generate_notification_events(
                kind="system",
                db=session,
                organization_id=org.id,
                _=object(),
            )
            self.assertEqual(first_generate["system_events"], 0)

            status = (
                await session.execute(
                    select(ServiceStatus).where(ServiceStatus.service == "okx")
                )
            ).scalar_one()
            status.is_healthy = False
            status.last_error = "401 Unauthorized"
            status.last_check = datetime.now(timezone.utc)
            await session.commit()

            second_generate = await generate_notification_events(
                kind="system",
                db=session,
                organization_id=org.id,
                _=object(),
            )
            self.assertEqual(second_generate["system_events"], 1)
            self.assertIn("sync_job_events", second_generate)

            events = await list_notification_events(
                status=None,
                limit=50,
                offset=0,
                db=session,
                organization_id=org.id,
                user=user,
                _=object(),
            )
            self.assertEqual(events.total_count, 1)
            event = events.events[0]
            self.assertEqual(event.status, "pending")

            marked = await mark_notification_event_sent(
                event.id,
                db=session,
                organization_id=org.id,
                user=user,
                _=object(),
            )
            self.assertEqual(marked, {"status": "ok"})

            sent_events = await list_notification_events(
                status="sent",
                limit=50,
                offset=0,
                db=session,
                organization_id=org.id,
                user=user,
                _=object(),
            )
            self.assertEqual(sent_events.total_count, 1)

    async def test_transaction_generation_contract_skips_existing_baseline(self):
        async with self.session_maker() as session:
            org, user = await self._org_user(session)
            session.add(ServiceStatus(organization_id=org.id, service="bingx", is_healthy=True))
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

            await update_notification_settings(
                payload=NotificationSettingsUpdate(transaction_enabled=True),
                db=session,
                organization_id=org.id,
                user=user,
                _=object(),
            )
            baseline_generate = await generate_notification_events(
                kind="transactions",
                db=session,
                organization_id=org.id,
                _=object(),
            )
            self.assertEqual(baseline_generate["transaction_events"], 0)

            session.add(
                Transaction(
                    organization_id=org.id,
                    tx_id="new",
                    service="bingx",
                    tx_type="deposit",
                    currency="USDT",
                    amount=2,
                    status="ok",
                    tx_timestamp=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            generated = await generate_notification_events(
                kind="transactions",
                db=session,
                organization_id=org.id,
                _=object(),
            )
            self.assertEqual(generated["transaction_events"], 1)


if __name__ == "__main__":
    unittest.main()
