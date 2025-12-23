import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import async_session_maker, init_db
from app.routers.balances import router as balances_router
from app.routers.transactions import router as transactions_router
from app.services.balance_service import BalanceService
from app.services.ccxt_manager import ccxt_manager
from app.services.okx_wallet import okx_wallet_service

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


async def background_refresh_loop():
    """Background task to refresh balances periodically"""
    refresh_interval = settings.balance_cache_ttl  # seconds
    logger.info(f"Background refresh started, interval: {refresh_interval}s")

    # Initial delay before first refresh
    await asyncio.sleep(10)

    while True:
        try:
            logger.info("Background refresh: starting balance update")
            async with async_session_maker() as db:
                service = BalanceService(db)
                result = await service.get_all_balances(force_refresh=True)
                # logger.info(
                #     f"Background refresh completed: {len(result.services)} services, "
                #     f"total: ${result.total_usd:.2f}"
                # )
        except asyncio.CancelledError:
            logger.info("Background refresh task cancelled")
            break
        except Exception as e:
            logger.error(f"Background refresh error: {e}")

        await asyncio.sleep(refresh_interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _background_task
    logger.info("Starting Balance Tracker API")
    await init_db()
    logger.info("Database initialized")

    # Start background refresh task
    _background_task = asyncio.create_task(background_refresh_loop())
    logger.info("Background refresh task started")

    yield

    logger.info("Shutting down Balance Tracker API")

    # Cancel background task
    if _background_task:
        _background_task.cancel()
        try:
            await _background_task
        except asyncio.CancelledError:
            pass

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(balances_router)
app.include_router(transactions_router)


@app.get("/")
async def root():
    return {"status": "ok", "service": "balance-tracker"}


@app.get("/ping")
async def ping():
    return {"pong": True}
