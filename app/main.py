import asyncio
import logging
import time
from contextlib import asynccontextmanager

from sqlalchemy import text

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import async_session_maker, engine, init_db, is_sqlite
from app.core.middleware import RequestContextMiddleware
from app.core.security import SecurityError, decode_access_token
from app.repositories.auth import AuthRepository
from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.balances import router as balances_router
from app.routers.billing import router as billing_router
from app.routers.integrations import router as integrations_router
from app.routers.notifications import router as notifications_router
from app.routers.observability import router as observability_router
from app.routers.transactions import router as transactions_router
from app.services.balance_service import BalanceService
from app.services.ccxt_manager import ccxt_manager
from app.services.entitlements_service import EntitlementsService
from app.services.metrics_service import metrics_service
from app.services.okx_wallet import okx_wallet_service
from app.services.refresh_orchestrator import RefreshOrchestrator
from app.services.sync_job_service import SyncJobService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress verbose logging from ccxt and aiohttp
logging.getLogger("ccxt").setLevel(logging.WARNING)
logging.getLogger("ccxtpro").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

settings = get_settings()

# Background task control
_background_task: asyncio.Task | None = None
_sync_worker_tasks: list[asyncio.Task] = []


async def _repair_sqlite_service_status_index() -> None:
    if not is_sqlite:
        return

    async with engine.begin() as conn:
        rows = (
            await conn.execute(text("PRAGMA index_list('service_status')"))
        ).fetchall()
        index_names = {row[1] for row in rows}

        if "ix_service_status_org_service" in index_names:
            return

        if "ix_service_status_service" in index_names:
            logger.info(
                "Repairing legacy SQLite index ix_service_status_service -> ix_service_status_org_service"
            )
            await conn.execute(text("DROP INDEX IF EXISTS ix_service_status_service"))

        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_service_status_org_service "
                "ON service_status (organization_id, service)"
            )
        )
        logger.info("SQLite service_status index verified")


def _is_refresh_loop_enabled() -> bool:
    if settings.enable_legacy_background_refresh_loop is not None:
        return settings.enable_legacy_background_refresh_loop
    return settings.enable_inprocess_refresh_loop


def _is_worker_enabled() -> bool:
    if settings.enable_inprocess_sync_worker is not None:
        return settings.enable_inprocess_sync_worker
    return settings.enable_worker


async def background_refresh_loop():
    """Background task that enforces plan-based auto-refresh cadence."""
    poll_interval = max(1.0, settings.background_refresh_poll_interval_seconds)
    logger.info(
        "Background refresh started, poll_interval=%ss, cadence=per-plan",
        poll_interval,
    )

    # Initial delay before first refresh
    await asyncio.sleep(10)

    while True:
        try:
            logger.info("Background refresh: starting balance update")
            async with async_session_maker() as db:
                auth_repo = AuthRepository(db)
                organizations = await auth_repo.list_active_organizations()
                if not organizations:
                    default_org = await auth_repo.ensure_default_organization()
                    organizations = [default_org]

                total_services = 0
                total_usd = 0.0
                for organization in organizations:
                    try:
                        service = BalanceService(db, organization_id=organization.id)
                        updated, failed = await service.refresh_all()
                        if not updated and not failed:
                            logger.debug(
                                "Background refresh: org %s has no active integrations",
                                organization.id,
                            )
                            continue
                        result = await service.get_all_balances(force_refresh=False)
                        total_services += len(result.services)
                        total_usd += result.total_usd
                        logger.info(
                            "Background refresh completed for org %s: updated=%s failed=%s total_usd=%.2f",
                            organization.id,
                            len(updated),
                            len(failed),
                            result.total_usd,
                        )
                    except HTTPException as exc:
                        detail = exc.detail if isinstance(exc.detail, dict) else {}
                        logger.info(
                            "Background refresh skipped for org %s: next plan refresh not due%s",
                            organization.id,
                            (
                                f", retry_after_seconds={detail.get('retry_after_seconds')}"
                                if detail.get("retry_after_seconds") is not None
                                else ""
                            ),
                        )
                        continue

                logger.info(
                    f"Background refresh completed: {total_services} services, "
                    f"total: ${total_usd:.2f}"
                )
        except asyncio.CancelledError:
            logger.info("Background refresh task cancelled")
            break
        except Exception as e:
            logger.error(f"Background refresh error: {e}")

        await asyncio.sleep(poll_interval)


async def inprocess_sync_worker_loop(worker_id: int):
    logger.info(
        "In-process sync worker %s started, poll_interval=%ss",
        worker_id,
        settings.sync_worker_poll_interval_seconds,
    )

    last_recovery_at = 0.0
    recovery_interval = max(30.0, float(settings.sync_job_stale_recovery_interval_seconds or 0.0))

    while True:
        try:
            with metrics_service.time("worker_run_next_job_seconds"):
                async with async_session_maker() as db:
                    now = time.monotonic()
                    if now - last_recovery_at >= recovery_interval:
                        recovered = await SyncJobService(db).recover_stale_running_jobs()
                        last_recovery_at = now
                        if recovered:
                            logger.warning(
                                "Recovered stale running sync jobs count=%s worker_id=%s",
                                recovered,
                                worker_id,
                            )
                    job = await RefreshOrchestrator(db).run_next_job()

            if job is None:
                await asyncio.sleep(settings.sync_worker_poll_interval_seconds)
            elif job.status == "completed":
                metrics_service.inc("sync_jobs_completed_total")
            else:
                metrics_service.inc("sync_jobs_failed_total")
        except asyncio.CancelledError:
            logger.info("In-process sync worker %s task cancelled", worker_id)
            break
        except Exception as exc:
            logger.error("In-process sync worker %s error: %s", worker_id, exc)
            metrics_service.inc("sync_worker_errors_total")
            await asyncio.sleep(settings.sync_worker_poll_interval_seconds)


async def _ensure_api_token_identity() -> None:
    if not settings.api_token:
        return

    try:
        payload = decode_access_token(settings.api_token)
        user_id = int(payload.get("sub"))
        organization_id = int(payload.get("org") or settings.default_org_id)
    except (SecurityError, TypeError, ValueError) as exc:
        logger.warning("Skipping API_TOKEN bootstrap identity: %s", exc)
        return

    async with async_session_maker() as db:
        await AuthRepository(db).ensure_service_identity(
            user_id=user_id,
            organization_id=organization_id,
            email=f"api-user-{user_id}@local.invalid",
            full_name="API Service User",
            role="owner",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _background_task, _sync_worker_tasks
    logger.info("Starting Balance Tracker API")
    await init_db()
    await _repair_sqlite_service_status_index()
    logger.info("Database initialized")

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

    await _ensure_api_token_identity()
    logger.info("Default plans and subscriptions ensured")

    if _is_refresh_loop_enabled():
        _background_task = asyncio.create_task(background_refresh_loop())
        logger.info("Background refresh task started")
    else:
        logger.info("Background refresh task disabled by feature flag")

    if _is_worker_enabled():
        worker_count = max(1, settings.job_parallelism)
        for worker_id in range(1, worker_count + 1):
            _sync_worker_tasks.append(
                asyncio.create_task(inprocess_sync_worker_loop(worker_id=worker_id))
            )
        logger.info(
            "In-process sync workers started: %s (job_parallelism)",
            worker_count,
        )
    else:
        logger.info("In-process sync worker disabled by feature flag")

    yield

    logger.info("Shutting down Balance Tracker API")

    if _background_task:
        _background_task.cancel()
        try:
            await _background_task
        except asyncio.CancelledError:
            pass

    for worker_task in _sync_worker_tasks:
        worker_task.cancel()
    for worker_task in _sync_worker_tasks:
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    _sync_worker_tasks.clear()

    await ccxt_manager.close_all()
    await okx_wallet_service.close()
    logger.info("Cleanup completed")


app = FastAPI(
    title="Balance Tracker API",
    description="Multi-exchange portfolio balance tracker",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id", "X-Org-Id", "X-User-Id", "X-Organization-Id", "X-Event-Id"],
)
app.add_middleware(RequestContextMiddleware)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(balances_router)
app.include_router(transactions_router)
app.include_router(integrations_router)
app.include_router(notifications_router)
app.include_router(billing_router)
app.include_router(observability_router)


@app.get("/")
async def root():
    return {"status": "ok", "service": "balance-tracker"}


@app.get("/ping")
async def ping():
    return {"pong": True}
