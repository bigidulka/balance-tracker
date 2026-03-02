from datetime import datetime, timezone

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.services.metrics_service import metrics_service

router = APIRouter(tags=["observability"])
settings = get_settings()


@router.get("/metrics")
async def get_metrics():
    return metrics_service.full_snapshot()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "balance-tracker",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/readiness")
async def readiness():
    db_ok = False
    try:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    readiness_status = "ready" if db_ok else "not_ready"
    payload = {
        "status": readiness_status,
        "checks": {"database": "ok" if db_ok else "error"},
        "settings": {
            "enable_inprocess_refresh_loop": settings.legacy_background_refresh_loop_enabled,
            "enable_worker": settings.inprocess_sync_worker_enabled,
            "exchange_parallelism": settings.exchange_parallelism,
            "job_parallelism": settings.job_parallelism,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if db_ok:
        return payload

    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
