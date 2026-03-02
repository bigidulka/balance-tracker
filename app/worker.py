import asyncio
import logging

from app.core.config import get_settings
from app.core.database import async_session_maker, init_db
from app.repositories.auth import AuthRepository
from app.services.ccxt_manager import ccxt_manager
from app.services.entitlements_service import EntitlementsService
from app.services.okx_wallet import okx_wallet_service
from app.services.refresh_orchestrator import RefreshOrchestrator

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


async def worker_loop() -> None:
    logger.info(
        "Sync worker started, poll_interval=%ss, enabled=%s",
        settings.sync_worker_poll_interval_seconds,
        settings.inprocess_sync_worker_enabled,
    )

    while True:
        try:
            async with async_session_maker() as db:
                job = await RefreshOrchestrator(db).run_next_job()
                if job is not None:
                    logger.info(
                        "Processed sync job id=%s organization_id=%s status=%s",
                        job.id,
                        job.organization_id,
                        job.status,
                    )

            if job is None:
                await asyncio.sleep(settings.sync_worker_poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Worker loop error: %s", exc)
            await asyncio.sleep(settings.sync_worker_poll_interval_seconds)


async def main() -> None:
    logger.info("Starting Balance Tracker worker")

    if not settings.inprocess_sync_worker_enabled:
        logger.info("Worker disabled by feature flag")
        return

    await init_db()
    await _ensure_defaults()

    try:
        await worker_loop()
    finally:
        await ccxt_manager.close_all()
        await okx_wallet_service.close()
        logger.info("Worker shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
