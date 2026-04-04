from datetime import datetime
from typing import Optional

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_organization_id, require_role
from app.core.request_context import RequestContext, get_request_context
from app.schemas.balance import (
    TransactionListResponse,
    TransactionsSummary,
    TransactionsRefreshResponse,
)
from app.services.audit_log_service import AuditLogService
from app.services.logging_context import get_request_logger, request_log_context
from app.services.metrics_service import metrics_service
from app.services.transaction_service import TransactionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


@router.get("", response_model=TransactionListResponse)
async def get_transactions(
    service: Optional[str] = Query(None, description="Filter by service/exchange name"),
    integration_id: Optional[int] = Query(None, description="Filter by integration id"),
    tx_type: Optional[str] = Query(
        None, description="Filter by type: deposit or withdrawal"
    ),
    status: Optional[str] = Query(
        None, description="Filter by status: pending, ok, failed, canceled"
    ),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=500, description="Maximum entries to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    tx_service = TransactionService(db, organization_id=organization_id)
    return await tx_service.get_transactions(
        service=service,
        integration_id=integration_id,
        tx_type=tx_type,
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@router.get("/deposits", response_model=TransactionListResponse)
async def get_deposits(
    service: Optional[str] = Query(None, description="Filter by service/exchange name"),
    integration_id: Optional[int] = Query(None, description="Filter by integration id"),
    status: Optional[str] = Query(None, description="Filter by status"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=500, description="Maximum entries to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    tx_service = TransactionService(db, organization_id=organization_id)
    return await tx_service.get_transactions(
        service=service,
        integration_id=integration_id,
        tx_type="deposit",
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@router.get("/withdrawals", response_model=TransactionListResponse)
async def get_withdrawals(
    service: Optional[str] = Query(None, description="Filter by service/exchange name"),
    integration_id: Optional[int] = Query(None, description="Filter by integration id"),
    status: Optional[str] = Query(None, description="Filter by status"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=500, description="Maximum entries to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    tx_service = TransactionService(db, organization_id=organization_id)
    return await tx_service.get_transactions(
        service=service,
        integration_id=integration_id,
        tx_type="withdrawal",
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@router.get("/summary/{service}", response_model=TransactionsSummary)
async def get_service_summary(
    service: str,
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("viewer")),
):
    tx_service = TransactionService(db, organization_id=organization_id)
    return await tx_service.get_service_summary(service)


@router.post("/refresh", response_model=TransactionsRefreshResponse)
async def refresh_transactions(
    since_hours: int = Query(
        168, ge=1, le=720, description="Hours to look back (default: 7 days)"
    ),
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    ctx: RequestContext = Depends(get_request_context),
    _: object = Depends(require_role("member")),
):
    tx_service = TransactionService(db, organization_id=organization_id)
    result = await tx_service.refresh_transactions(since_hours=since_hours)

    await AuditLogService(db).log_event(
        organization_id=organization_id,
        user_id=ctx.user_id,
        request_id=ctx.request_id,
        action="transactions.refresh",
        resource_type="transactions",
        details={"since_hours": since_hours, "status": result.status},
    )
    metrics_service.inc("transactions_refresh_total")

    log = get_request_logger(
        logger,
        request_log_context(ctx.request_id, organization_id, ctx.user_id),
    )
    log.info("transactions_refresh")

    return result
