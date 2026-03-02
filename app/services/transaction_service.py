import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.repositories.balance import TransactionRepository, ServiceStatusRepository
from app.schemas.balance import (
    TransactionSchema,
    TransactionListResponse,
    TransactionsSummary,
    TransactionsRefreshResponse,
)
from app.services.ccxt_manager import ccxt_manager
from app.services.entitlements_service import EntitlementsService

logger = logging.getLogger(__name__)
settings = get_settings()

_transactions_refresh_inflight: dict[str, asyncio.Task] = {}
_transactions_refresh_inflight_lock = asyncio.Lock()


class TransactionService:
    """Сервис для работы с транзакциями (вводы/выводы)"""

    def __init__(self, session: AsyncSession, organization_id: int = 1):
        self.session = session
        self.organization_id = organization_id
        self.tx_repo = TransactionRepository(session)
        self.status_repo = ServiceStatusRepository(session)
        self.entitlements = EntitlementsService(session)

    async def get_transactions(
        self,
        service: Optional[str] = None,
        tx_type: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> TransactionListResponse:
        """Получить список транзакций с фильтрами"""

        transactions = await self.tx_repo.get_transactions(
            organization_id=self.organization_id,
            service=service,
            tx_type=tx_type,
            status=status,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )

        total_count = await self.tx_repo.get_transaction_count(
            organization_id=self.organization_id,
            service=service,
            tx_type=tx_type,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        tx_schemas = [
            TransactionSchema(
                id=tx.id,
                tx_id=tx.tx_id,
                service=tx.service,
                tx_type=tx.tx_type,
                currency=tx.currency,
                amount=tx.amount,
                fee=tx.fee,
                fee_currency=tx.fee_currency,
                network=tx.network,
                address=tx.address,
                address_from=tx.address_from,
                address_to=tx.address_to,
                tag=tx.tag,
                status=tx.status,
                txid=tx.txid,
                tx_timestamp=tx.tx_timestamp,
                notified=tx.notified,
                created_at=tx.created_at,
                updated_at=tx.updated_at,
            )
            for tx in transactions
        ]

        return TransactionListResponse(
            service=service,
            tx_type=tx_type,
            transactions=tx_schemas,
            total_count=total_count,
        )

    async def get_service_summary(self, service: str) -> TransactionsSummary:
        """Получить сводку по транзакциям для сервиса"""

        total_deposits = await self.tx_repo.get_transaction_count(
            organization_id=self.organization_id,
            service=service, tx_type="deposit"
        )
        total_withdrawals = await self.tx_repo.get_transaction_count(
            organization_id=self.organization_id,
            service=service, tx_type="withdrawal"
        )
        pending_deposits = await self.tx_repo.get_transaction_count(
            organization_id=self.organization_id,
            service=service, tx_type="deposit", status="pending"
        )
        pending_withdrawals = await self.tx_repo.get_transaction_count(
            organization_id=self.organization_id,
            service=service, tx_type="withdrawal", status="pending"
        )

        last_deposit = await self.tx_repo.get_last_transaction_timestamp(
            service=service,
            tx_type="deposit",
            organization_id=self.organization_id,
        )
        last_withdrawal = await self.tx_repo.get_last_transaction_timestamp(
            service=service,
            tx_type="withdrawal",
            organization_id=self.organization_id,
        )

        return TransactionsSummary(
            service=service,
            total_deposits=total_deposits,
            total_withdrawals=total_withdrawals,
            pending_deposits=pending_deposits,
            pending_withdrawals=pending_withdrawals,
            last_deposit=last_deposit,
            last_withdrawal=last_withdrawal,
        )

    async def refresh_transactions(
        self,
        exchange_ids: Optional[list[str]] = None,
        since_hours: int = 24 * 7,  # По умолчанию за последнюю неделю
    ) -> TransactionsRefreshResponse:
        """Обновить транзакции со всех бирж"""

        await self.entitlements.ensure_refresh_interval_for_organization(self.organization_id)

        if exchange_ids is None:
            exchange_ids = settings.get_active_exchanges()

        exchange_ids = sorted(exchange_ids)
        inflight_key = f"org:{self.organization_id}:since:{since_hours}:exchanges:{','.join(exchange_ids)}"

        async def _refresh_impl() -> TransactionsRefreshResponse:
            since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

            new_count = 0
            updated_count = 0
            checked_services = []
            failed_services = []

            results = await ccxt_manager.fetch_transactions_all_exchanges(
                exchange_ids=exchange_ids, since=since, limit=100
            )

            for exchange_id, result in results.items():
                if isinstance(result, Exception):
                    logger.warning(
                        f"Failed to fetch transactions from {exchange_id}: {result}"
                    )
                    failed_services.append(exchange_id)
                    continue

                checked_services.append(exchange_id)

                try:
                    exchange_new, exchange_updated = await self.tx_repo.save_transactions_batch(
                        result, organization_id=self.organization_id
                    )
                    new_count += exchange_new
                    updated_count += exchange_updated
                except Exception as e:
                    logger.error(
                        f"Failed to save transactions batch for {exchange_id}: {e}"
                    )
                    for tx_data in result:
                        try:
                            _, is_new = await self.tx_repo.save_transaction(
                                tx_data, organization_id=self.organization_id
                            )
                            if is_new:
                                new_count += 1
                            else:
                                updated_count += 1
                        except Exception as item_exc:
                            logger.error(
                                f"Failed to save transaction {tx_data.tx_id}: {item_exc}"
                            )

            status = (
                "ok" if not failed_services else "partial" if checked_services else "error"
            )
            message = f"Found {new_count} new and {updated_count} updated transactions"

            return TransactionsRefreshResponse(
                status=status,
                message=message,
                new_transactions=new_count,
                updated_transactions=updated_count,
                services_checked=checked_services,
                failed_services=failed_services,
            )

        if not settings.enable_ccxt_singleflight:
            return await _refresh_impl()

        async def _cleanup_inflight(done_task: asyncio.Task) -> None:
            async with _transactions_refresh_inflight_lock:
                if _transactions_refresh_inflight.get(inflight_key) is done_task:
                    _transactions_refresh_inflight.pop(inflight_key, None)

        async with _transactions_refresh_inflight_lock:
            task = _transactions_refresh_inflight.get(inflight_key)
            if task is None:
                task = asyncio.create_task(_refresh_impl())
                _transactions_refresh_inflight[inflight_key] = task
                task.add_done_callback(
                    lambda done_task: asyncio.create_task(_cleanup_inflight(done_task))
                )

        return await asyncio.shield(task)

    async def get_unnotified_transactions(self) -> list[TransactionSchema]:
        """Получить транзакции, для которых не было отправлено уведомление"""

        transactions = await self.tx_repo.get_unnotified_transactions(
            status="ok", organization_id=self.organization_id
        )

        return [
            TransactionSchema(
                id=tx.id,
                tx_id=tx.tx_id,
                service=tx.service,
                tx_type=tx.tx_type,
                currency=tx.currency,
                amount=tx.amount,
                fee=tx.fee,
                fee_currency=tx.fee_currency,
                network=tx.network,
                address=tx.address,
                address_from=tx.address_from,
                address_to=tx.address_to,
                tag=tx.tag,
                status=tx.status,
                txid=tx.txid,
                tx_timestamp=tx.tx_timestamp,
                notified=tx.notified,
                created_at=tx.created_at,
                updated_at=tx.updated_at,
            )
            for tx in transactions
        ]

    async def mark_transactions_as_notified(self, transaction_ids: list[int]) -> int:
        """Отметить транзакции как обработанные (уведомления отправлены)"""
        return await self.tx_repo.mark_multiple_as_notified(
            transaction_ids, organization_id=self.organization_id
        )
