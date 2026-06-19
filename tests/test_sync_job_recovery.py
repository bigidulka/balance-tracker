import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.balance import Organization, SyncJob
from app.services.sync_job_service import SyncJobService


class SyncJobRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_recover_stale_running_jobs_uses_provider_timeout_ceiling_by_default(self):
        async with self.session_maker() as session:
            org = Organization(name="Provider Timeout Org", slug="provider-timeout-org")
            session.add(org)
            await session.flush()

            now = datetime.now(timezone.utc)
            stale_after_provider_timeout = SyncJob(
                organization_id=org.id,
                integration_id=None,
                job_type="refresh",
                status="running",
                payload={},
                result={},
                started_at=now - timedelta(minutes=4),
            )
            fresh = SyncJob(
                organization_id=org.id,
                integration_id=None,
                job_type="refresh",
                status="running",
                payload={},
                result={},
                started_at=now - timedelta(seconds=30),
            )
            session.add_all([stale_after_provider_timeout, fresh])
            await session.commit()

            recovered = await SyncJobService(session).recover_stale_running_jobs()
            await session.refresh(stale_after_provider_timeout)
            await session.refresh(fresh)

            self.assertEqual(recovered, 1)
            self.assertEqual(stale_after_provider_timeout.status, "failed")
            self.assertEqual(fresh.status, "running")

    async def test_recover_stale_running_jobs_marks_only_stale_jobs_failed(self):
        async with self.session_maker() as session:
            org = Organization(name="Recovery Org", slug="recovery-org")
            session.add(org)
            await session.flush()

            now = datetime.now(timezone.utc)
            stale = SyncJob(
                organization_id=org.id,
                integration_id=None,
                job_type="refresh",
                status="running",
                payload={},
                result={},
                started_at=now - timedelta(minutes=20),
            )
            fresh = SyncJob(
                organization_id=org.id,
                integration_id=None,
                job_type="refresh",
                status="running",
                payload={},
                result={},
                started_at=now - timedelta(seconds=30),
            )
            session.add_all([stale, fresh])
            await session.commit()

            recovered = await SyncJobService(session).recover_stale_running_jobs(
                timeout_seconds=900
            )
            await session.refresh(stale)
            await session.refresh(fresh)

            self.assertEqual(recovered, 1)
            self.assertEqual(stale.status, "failed")
            self.assertEqual(
                stale.result, {"status": "failed", "code": "stale_running_job_recovered"}
            )
            self.assertIsNotNone(stale.finished_at)
            self.assertEqual(fresh.status, "running")
            self.assertIsNone(fresh.finished_at)


if __name__ == "__main__":
    unittest.main()
