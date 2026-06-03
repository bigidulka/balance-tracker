import asyncio
import logging

from app.core.config import get_settings
from app.core.database import async_session_maker, init_db
from app.repositories.auth import AuthRepository
from app.services.ccxt_manager import ccxt_manager
from app.services.entitlements_service import EntitlementsService
from app.services.okx_wallet import okx_wallet_service
from app.services.refresh_orchestrator import RefreshOrchestrator
from app.services.sync_job_service import SyncJobService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()


async def _ensure_defaults() -> None:
    async with async_session_maker() as db:
        entitlements = EntitlementsService(db)
        await entitlements.ensure_default_plans()

        auth_repo = AuthRepository(db)
        organizations = await auth_repo.list_active_organizations()
        if not organizations:
            default_org = await auth_repo.ensure_default_organization()
            organizations = [default_org]

        for organization in organizations:
            await entitlements.ensure_default_subscription(organization.id)


async def worker_loop(worker_id: int) -> None:
    logger.info(
        "Sync worker %s started, poll_interval=%ss, enabled=%s",
        worker_id,
        settings.sync_worker_poll_interval_seconds,
        settings.inprocess_sync_worker_enabled,
    )

    while True:
        try:
            async with async_session_maker() as db:
                job = await RefreshOrchestrator(db).run_next_job()
                if job is not None:
                    logger.info(
                        "Processed sync job id=%s organization_id=%s status=%s worker_id=%s",
                        job.id,
                        job.organization_id,
                        job.status,
                        worker_id,
                    )

            if job is None:
                await asyncio.sleep(settings.sync_worker_poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Worker %s loop error: %s", worker_id, exc)
            await asyncio.sleep(settings.sync_worker_poll_interval_seconds)


async def main() -> None:
    logger.info("Starting Balance Tracker worker")

    if not settings.inprocess_sync_worker_enabled:
        logger.info("Worker disabled by feature flag")
        return

    await init_db()
    await _ensure_defaults()

    async with async_session_maker() as db:
        recovered = await SyncJobService(db).recover_stale_running_jobs()
        if recovered:
            logger.warning("Recovered stale running sync jobs count=%s", recovered)

    worker_count = max(1, settings.job_parallelism)
    logger.info("Starting %s sync worker loop(s)", worker_count)
    tasks = [asyncio.create_task(worker_loop(worker_id)) for worker_id in range(1, worker_count + 1)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await ccxt_manager.close_all()
        await okx_wallet_service.close()
        logger.info("Worker shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
