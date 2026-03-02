from datetime import datetime, timedelta, timezone
from typing import Optional

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import get_current_organization_id, require_role
from app.core.request_context import RequestContext, get_request_context
from app.repositories.balance import (
    BalanceRepository,
    ServiceStatusRepository,
    TransactionRepository,
)
from app.schemas.balance import (
    AccountBalanceSchema,
    AssetSchema,
    DashboardSummaryResponse,
    HealthResponse,
    HistoryEntrySchema,
    HistoryResponse,
    PortfolioResponse,
    RefreshResponse,
    ServiceBalanceSchema,
    ServiceHealthSchema,
)
from app.services.audit_log_service import AuditLogService
from app.services.balance_service import BalanceService
from app.services.entitlements_service import EntitlementsService
from app.services.logging_context import get_request_logger, request_log_context
from app.services.integration_service import IntegrationService
from app.services.metrics_service import metrics_service
from app.services.refresh_orchestrator import RefreshOrchestrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["balances"])
settings = get_settings()


@router.get("/balances", response_model=PortfolioResponse)
async def get_balances(
    force_refresh: bool = Query(False, description="Force refresh from exchanges"),
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    service = BalanceService(db, organization_id=organization_id)
    return await service.get_all_balances(force_refresh=force_refresh)


@router.get("/balances/cached", response_model=PortfolioResponse)
async def get_cached_balances(
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    repo = BalanceRepository(db)
    balances = await repo.get_all_latest_balances(organization_id=organization_id)

    services = []
    total_usd = 0.0

    for balance in balances:
        assets = [AssetSchema(**a) for a in (balance.assets or [])]

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
    organization_id: int = Depends(get_current_organization_id),
    ctx: RequestContext = Depends(get_request_context),
    _: object = Depends(require_role("member")),
):
    updated, failed = await RefreshOrchestrator(db).refresh_all_now(organization_id)

    status = "ok" if not failed else "partial" if updated else "error"
    message = f"Updated {len(updated)} services"
    if failed:
        message += f", {len(failed)} failed"

    await AuditLogService(db).log_event(
        organization_id=organization_id,
        user_id=ctx.user_id,
        request_id=ctx.request_id,
        action="balances.refresh",
        resource_type="balances",
        details={"updated_services": updated, "failed_services": failed},
    )
    metrics_service.inc("balances_refresh_total")

    log = get_request_logger(
        logger,
        request_log_context(ctx.request_id, organization_id, ctx.user_id),
    )
    log.info("balances_refresh")

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
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    repo = BalanceRepository(db)

    entries = await repo.get_history(
        organization_id=organization_id,
        service=service,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )

    total_count = await repo.get_history_count(
        organization_id=organization_id,
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


@router.get("/entitlements")
async def get_entitlements(
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    service = EntitlementsService(db)
    return await service.get_effective_entitlements(organization_id)


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    balance_repo = BalanceRepository(db)
    balances = await balance_repo.get_all_latest_balances(organization_id=organization_id)

    total_usd = 0.0
    spot_total = 0.0
    futures_total = 0.0
    dex_total = 0.0
    exchanges_count = 0
    latest_updated_at: datetime | None = None

    for balance in balances:
        total_usd += float(balance.total_usd)
        updated_at = balance.updated_at
        if latest_updated_at is None or updated_at > latest_updated_at:
            latest_updated_at = updated_at

        service_name = (balance.service or "").lower()
        is_dex = service_name.startswith("okx_wallet")

        if is_dex:
            dex_total += float(balance.total_usd)
            continue

        exchanges_count += 1
        accounts = balance.accounts or []
        if accounts:
            has_account_type = False
            for acc in accounts:
                acc_type = (acc.get("account_type") or "").lower()
                acc_total = float(acc.get("total_usd") or 0.0)
                if acc_type == "futures":
                    futures_total += acc_total
                    has_account_type = True
                elif acc_type == "spot":
                    spot_total += acc_total
                    has_account_type = True
            if not has_account_type:
                spot_total += float(balance.total_usd)
        else:
            spot_total += float(balance.total_usd)

    now = datetime.now(timezone.utc)
    freshness = "No data"
    if latest_updated_at is not None:
        latest_dt = latest_updated_at
        if latest_dt.tzinfo is None:
            latest_dt = latest_dt.replace(tzinfo=timezone.utc)
        minutes = int((now - latest_dt).total_seconds() / 60)
        if minutes < 1:
            freshness = "updated_just_now"
        elif minutes < 5:
            freshness = f"updated_{minutes}m"
        elif minutes < 15:
            freshness = f"stale_{minutes}m"
        else:
            freshness = f"old_{minutes}m"

    entitlements = await EntitlementsService(db).get_effective_entitlements(organization_id)

    integrations = await IntegrationService(db).list_integrations(
        organization_id=organization_id,
        include_inactive=True,
    )
    integrations_active = sum(1 for i in integrations if i.is_active)
    integrations_inactive = len(integrations) - integrations_active

    tx_repo = TransactionRepository(db)
    tx_start = now - timedelta(hours=24)
    transactions_total_24h = await tx_repo.get_transaction_count(
        organization_id=organization_id,
        start_date=tx_start,
    )
    transactions_pending_24h = await tx_repo.get_transaction_count(
        organization_id=organization_id,
        start_date=tx_start,
        status="pending",
    )
    transactions_failed_24h = await tx_repo.get_transaction_count(
        organization_id=organization_id,
        start_date=tx_start,
        status="failed",
    )

    return DashboardSummaryResponse(
        total_usd=total_usd,
        exchanges_count=exchanges_count,
        spot_total=spot_total,
        futures_total=futures_total,
        dex_total=dex_total,
        freshness=freshness,
        plan=entitlements.get("plan", {}),
        capabilities=entitlements.get("capabilities", {}),
        throttling=entitlements.get("throttling", {}),
        integrations={
            "total": len(integrations),
            "active": integrations_active,
            "inactive": integrations_inactive,
        },
        transactions_24h={
            "total": transactions_total_24h,
            "pending": transactions_pending_24h,
            "failed": transactions_failed_24h,
        },
        timestamp=now,
    )


@router.get("/health", response_model=HealthResponse)
async def get_health(
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    repo = ServiceStatusRepository(db)
    statuses = await repo.get_all_statuses(organization_id=organization_id)

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
