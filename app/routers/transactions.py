from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.balance import (
    TransactionListResponse,
    TransactionsSummary,
    TransactionsRefreshResponse,
)
from app.services.transaction_service import TransactionService

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


@router.get("", response_model=TransactionListResponse)
async def get_transactions(
    service: Optional[str] = Query(None, description="Filter by service/exchange name"),
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
):
    """
    Получить список транзакций с фильтрами.

    Поддерживаемые фильтры:
    - service: название биржи (binance, okx, bybit и т.д.)
    - tx_type: deposit (ввод) или withdrawal (вывод)
    - status: pending (в обработке), ok (завершено), failed (ошибка), canceled (отменено)
    - start_date, end_date: фильтр по дате
    """
    tx_service = TransactionService(db)
    return await tx_service.get_transactions(
        service=service,
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
    status: Optional[str] = Query(None, description="Filter by status"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=500, description="Maximum entries to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
):
    """Получить список только вводов (deposits)"""
    tx_service = TransactionService(db)
    return await tx_service.get_transactions(
        service=service,
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
    status: Optional[str] = Query(None, description="Filter by status"),
    start_date: Optional[datetime] = Query(None, description="Start date filter"),
    end_date: Optional[datetime] = Query(None, description="End date filter"),
    limit: int = Query(100, ge=1, le=500, description="Maximum entries to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
):
    """Получить список только выводов (withdrawals)"""
    tx_service = TransactionService(db)
    return await tx_service.get_transactions(
        service=service,
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
):
    """Получить сводку по транзакциям для конкретной биржи"""
    tx_service = TransactionService(db)
    return await tx_service.get_service_summary(service)


@router.post("/refresh", response_model=TransactionsRefreshResponse)
async def refresh_transactions(
    since_hours: int = Query(
        168, ge=1, le=720, description="Hours to look back (default: 7 days)"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Обновить транзакции со всех активных бирж.

    Параметры:
    - since_hours: сколько часов назад искать транзакции (по умолчанию 168 = 7 дней)
    """
    tx_service = TransactionService(db)
    return await tx_service.refresh_transactions(since_hours=since_hours)
