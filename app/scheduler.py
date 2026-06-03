import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import async_session_maker, init_db
from app.models.balance import Organization, SyncJob
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


async def _has_active_refresh_job(db, organization_id: int) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=max(60, settings.sync_job_running_timeout_seconds)
    )
    result = await db.execute(
        select(func.count(SyncJob.id)).where(
            SyncJob.organization_id == organization_id,
            SyncJob.job_type == "refresh",
            SyncJob.status.in_(["queued", "running"]),
            func.coalesce(SyncJob.started_at, SyncJob.queued_at) >= cutoff,
        )
    )
    return int(result.scalar_one() or 0) > 0


async def _refresh_interval_seconds(entitlements: dict) -> int | None:
    throttling = entitlements.get("throttling") if isinstance(entitlements, dict) else {}
    background = entitlements.get("background") if isinstance(entitlements, dict) else {}
    if isinstance(background, dict) and not bool(background.get("enabled", True)):
        return None
    value = 0
    if isinstance(throttling, dict):
        value = int(throttling.get("background_refresh_interval_seconds") or 0)
    if value <= 0 and isinstance(background, dict):
        value = int(background.get("refresh_interval_seconds") or 0)
    if value <= 0:
        return None
    return max(value, settings.refresh_scheduler_min_interval_seconds)


async def _schedule_once() -> int:
    now = datetime.now(timezone.utc)
    scheduled = 0
    async with async_session_maker() as db:
        recovered = await SyncJobService(db).recover_stale_running_jobs()
        if recovered:
            logger.warning("Recovered stale running sync jobs count=%s", recovered)

        result = await db.execute(
            select(Organization).where(Organization.is_active == True).order_by(Organization.id)
        )
        organizations = list(result.scalars().all())
        if not organizations:
            organizations = [await AuthRepository(db).ensure_default_organization()]

        for organization in organizations:
            if scheduled >= max(1, settings.refresh_scheduler_batch_size):
                break
            entitlements_service = EntitlementsService(db)
            entitlements = await entitlements_service.get_effective_entitlements(organization.id)
            interval_seconds = await _refresh_interval_seconds(entitlements)
            if interval_seconds is None:
                continue
            last_refresh_at = await entitlements_service.get_latest_successful_refresh_at(
                organization.id
            )
            if last_refresh_at is not None:
                if last_refresh_at.tzinfo is None:
                    last_refresh_at = last_refresh_at.replace(tzinfo=timezone.utc)
                elapsed = (now - last_refresh_at).total_seconds()
                if elapsed < interval_seconds:
                    continue

            if await _has_active_refresh_job(db, organization.id):
                continue

            jobs = await RefreshOrchestrator(db).queue_refresh_for_organization(
                organization_id=organization.id,
                payload={"source": "scheduler"},
            )
            if jobs:
                scheduled += 1
                logger.info(
                    "Scheduled refresh jobs organization_id=%s jobs=%s interval_seconds=%s",
                    organization.id,
                    len(jobs),
                    interval_seconds,
                )

    return scheduled


async def scheduler_loop() -> None:
    poll_interval = max(1.0, settings.refresh_scheduler_poll_interval_seconds)
    logger.info(
        "Refresh scheduler started, poll_interval=%ss, min_interval=%ss, batch_size=%s",
        poll_interval,
        settings.refresh_scheduler_min_interval_seconds,
        settings.refresh_scheduler_batch_size,
    )

    while True:
        try:
            scheduled = await _schedule_once()
            if scheduled == 0:
                logger.debug("Refresh scheduler: no organizations due")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Refresh scheduler error: %s", exc)
        await asyncio.sleep(poll_interval)


async def main() -> None:
    logger.info("Starting Balance Tracker refresh scheduler")
    if not settings.enable_refresh_scheduler:
        logger.info("Refresh scheduler disabled by feature flag")
        return

    await init_db()
    await _ensure_defaults()

    try:
        await scheduler_loop()
    finally:
        await ccxt_manager.close_all()
        await okx_wallet_service.close()
        logger.info("Refresh scheduler shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
