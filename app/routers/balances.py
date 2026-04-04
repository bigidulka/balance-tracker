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
    ChartPointSchema,
    DashboardSummaryResponse,
    HealthResponse,
    HistoryChartResponse,
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
from app.services.okx_wallet import okx_wallet_service
from app.services.refresh_orchestrator import RefreshOrchestrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["balances"])
settings = get_settings()


def _floor_bucket(value: datetime, interval: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    if interval == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    if interval == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unsupported interval: {interval}")


def _next_bucket(value: datetime, interval: str) -> datetime:
    if interval == "hour":
        return value + timedelta(hours=1)
    if interval == "day":
        return value + timedelta(days=1)
    raise ValueError(f"Unsupported interval: {interval}")


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
    active_integrations = await IntegrationService(db).list_integrations(
        organization_id=organization_id,
        include_inactive=False,
    )
    allowed_services: set[str] = set()
    for integration in active_integrations:
        kind = str(integration.kind or "").strip().lower()
        if kind == "cex":
            exchange_code = str(integration.exchange_code or "").strip().lower()
            if exchange_code:
                allowed_services.add(exchange_code)
            continue
        if kind == "dex":
            wallet_address = str(integration.wallet_address or "").strip()
            if wallet_address:
                allowed_services.add(IntegrationService._wallet_service_name(wallet_address))
                allowed_services.add(okx_wallet_service.legacy_service_name_for(wallet_address))

    services = []
    total_usd = 0.0

    for balance in balances:
        if balance.service not in allowed_services:
            continue
        if not settings.is_service_enabled(balance.service):
            continue
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
            integration_id=balance.integration_id,
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
    integration_id: Optional[int] = Query(None, description="Filter by integration id"),
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
        integration_id=integration_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        order="desc",
    )

    total_count = await repo.get_history_count(
        organization_id=organization_id,
        service=service,
        integration_id=integration_id,
        start_date=start_date,
        end_date=end_date,
    )

    history_entries = [
        HistoryEntrySchema(
            integration_id=getattr(entry, "integration_id", None),
            service=entry.service,
            total_usd=entry.total_usd,
            assets=[AssetSchema(**a) for a in (entry.assets or [])],
            accounts=[
                AccountBalanceSchema(
                    account_type=acc.get("account_type", "spot"),
                    assets=[AssetSchema(**a) for a in acc.get("assets", [])],
                    total_usd=acc.get("total_usd", 0),
                )
                for acc in (entry.accounts or [])
            ],
            actual=bool(getattr(entry, "actual", True)),
            created_at=entry.created_at,
        )
        for entry in entries
    ]

    return HistoryResponse(
        service=service,
        entries=history_entries,
        total_entries=total_count,
    )


@router.get("/history/chart", response_model=HistoryChartResponse)
async def get_history_chart(
    service: Optional[str] = Query(None, description="Filter by service name"),
    integration_id: Optional[int] = Query(None, description="Filter by integration id"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
    interval: str = Query("hour", pattern="^(hour|day)$"),
    fill: str = Query("forward", pattern="^(forward|none)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    repo = BalanceRepository(db)
    now = datetime.now(timezone.utc)
    effective_end = end_date or now
    effective_start = start_date or (effective_end - timedelta(days=1 if interval == "hour" else 30))

    points = await repo.get_history_points(
        organization_id=organization_id,
        service=service,
        integration_id=integration_id,
        start_date=effective_start,
        end_date=effective_end,
    )

    by_bucket: dict[datetime, object] = {}
    for entry in points:
        bucket = _floor_bucket(entry.created_at, interval)
        by_bucket[bucket] = entry

    chart_points: list[ChartPointSchema] = []
    cursor = _floor_bucket(effective_start, interval)
    end_bucket = _floor_bucket(effective_end, interval)
    last_seen = None

    while cursor <= end_bucket:
        entry = by_bucket.get(cursor)
        if entry is not None:
            last_seen = entry
            chart_points.append(
                ChartPointSchema(
                    bucket_start=cursor,
                    bucket_end=_next_bucket(cursor, interval),
                    total_usd=float(entry.total_usd),
                    actual=bool(getattr(entry, "actual", True)),
                    point_type="observed",
                )
            )
        elif fill == "forward" and last_seen is not None:
            chart_points.append(
                ChartPointSchema(
                    bucket_start=cursor,
                    bucket_end=_next_bucket(cursor, interval),
                    total_usd=float(last_seen.total_usd),
                    actual=bool(getattr(last_seen, "actual", False)),
                    point_type="filled",
                )
            )
        elif fill == "none":
            pass
        cursor = _next_bucket(cursor, interval)

    if order == "desc":
        chart_points = list(reversed(chart_points))

    return HistoryChartResponse(
        service=service,
        interval=interval,
        fill=fill,
        order=order,
        points=chart_points,
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
    active_integrations = await IntegrationService(db).list_integrations(
        organization_id=organization_id,
        include_inactive=False,
    )
    allowed_services: set[str] = set()
    for integration in active_integrations:
        kind = str(integration.kind or "").strip().lower()
        if kind == "cex":
            exchange_code = str(integration.exchange_code or "").strip().lower()
            if exchange_code:
                allowed_services.add(exchange_code)
            continue
        if kind == "dex":
            wallet_address = str(integration.wallet_address or "").strip()
            if wallet_address:
                allowed_services.add(IntegrationService._wallet_service_name(wallet_address))
                allowed_services.add(okx_wallet_service.legacy_service_name_for(wallet_address))

    total_usd = 0.0
    spot_total = 0.0
    futures_total = 0.0
    dex_total = 0.0
    exchanges_count = 0
    latest_updated_at: datetime | None = None

    for balance in balances:
        if balance.service not in allowed_services:
            continue
        if not settings.is_service_enabled(balance.service):
            continue
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
        latest_updated_at=latest_updated_at,
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
        if settings.is_service_enabled(status.service)
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
