from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.repositories.balance import BalanceRepository, ServiceStatusRepository
from app.schemas.balance import (
    AccountBalanceSchema,
    AssetSchema,
    HealthResponse,
    HistoryEntrySchema,
    HistoryResponse,
    PortfolioResponse,
    RefreshResponse,
    ServiceBalanceSchema,
    ServiceHealthSchema,
)
from app.services.balance_service import BalanceService

router = APIRouter(prefix="/api/v1", tags=["balances"])
settings = get_settings()


@router.get("/balances", response_model=PortfolioResponse)
async def get_balances(
    force_refresh: bool = Query(False, description="Force refresh from exchanges"),
    db: AsyncSession = Depends(get_db),
):
    service = BalanceService(db)
    return await service.get_all_balances(force_refresh=force_refresh)


@router.get("/balances/cached", response_model=PortfolioResponse)
async def get_cached_balances(
    db: AsyncSession = Depends(get_db),
):
    """Get balances from database only - instant response"""
    repo = BalanceRepository(db)
    balances = await repo.get_all_latest_balances()

    services = []
    total_usd = 0.0

    for balance in balances:
        assets = [AssetSchema(**a) for a in (balance.assets or [])]

        # Load accounts from DB if available
        accounts = []
        if balance.accounts:
            for acc in balance.accounts:
                acc_assets = [AssetSchema(**a) for a in acc.get("assets", [])]
                accounts.append(
                    AccountBalanceSchema(
                        account_type=acc.get("account_type", "spot"),
                        assets=acc_assets,
                        total_usd=acc.get("total_usd", 0),
                    )
                )

        svc_balance = ServiceBalanceSchema(
            service=balance.service,
            accounts=accounts,
            assets=assets,
            total_usd=balance.total_usd,
            updated_at=balance.updated_at,
            actual=balance.actual,
        )
        services.append(svc_balance)
        total_usd += balance.total_usd

    return PortfolioResponse(
        total_usd=total_usd,
        services=services,
        timestamp=datetime.now(timezone.utc),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_balances(
    db: AsyncSession = Depends(get_db),
):
    service = BalanceService(db)
    updated, failed = await service.refresh_all()

    status = "ok" if not failed else "partial" if updated else "error"
    message = f"Updated {len(updated)} services"
    if failed:
        message += f", {len(failed)} failed"

    return RefreshResponse(
        status=status,
        message=message,
        updated_services=updated,
        failed_services=failed,
    )


@router.get("/history", response_model=HistoryResponse)
async def get_history(
    service: Optional[str] = Query(None, description="Filter by service name"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum entries to return"),
    db: AsyncSession = Depends(get_db),
):
    repo = BalanceRepository(db)

    entries = await repo.get_history(
        service=service,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )

    total_count = await repo.get_history_count(
        service=service,
        start_date=start_date,
        end_date=end_date,
    )

    history_entries = [
        HistoryEntrySchema(
            service=entry.service,
            total_usd=entry.total_usd,
            assets=[AssetSchema(**a) for a in entry.assets],
            created_at=entry.created_at,
        )
        for entry in entries
    ]

    return HistoryResponse(
        service=service,
        entries=history_entries,
        total_entries=total_count,
    )


@router.get("/health", response_model=HealthResponse)
async def get_health(
    db: AsyncSession = Depends(get_db),
):
    repo = ServiceStatusRepository(db)
    statuses = await repo.get_all_statuses()

    service_health = [
        ServiceHealthSchema(
            service=status.service,
            is_healthy=status.is_healthy,
            last_error=status.last_error,
            last_check=status.last_check,
        )
        for status in statuses
    ]

    total = len(service_health)
    healthy = sum(1 for s in service_health if s.is_healthy)

    overall_status = (
        "healthy" if healthy == total else "degraded" if healthy > 0 else "unhealthy"
    )

    return HealthResponse(
        status=overall_status,
        services=service_health,
        total_services=total,
        healthy_services=healthy,
    )
