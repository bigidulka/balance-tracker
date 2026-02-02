import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import Balance, BalanceHistory, ServiceStatus, Transaction
from app.schemas.balance import (
    AccountBalanceSchema,
    AssetSchema,
    ServiceBalanceSchema,
    TransactionSchema,
)

logger = logging.getLogger(__name__)


class BalanceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_latest_balance(self, service: str) -> Optional[Balance]:
        query = (
            select(Balance)
            .where(Balance.service == service)
            .order_by(desc(Balance.updated_at))
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_all_latest_balances(self) -> list[Balance]:
        """Get the latest balance for each service"""
        services = await self.session.execute(select(Balance.service).distinct())
        service_names = [row[0] for row in services.fetchall()]

        balances = []
        for service_name in service_names:
            balance = await self.get_latest_balance(service_name)
            if balance:
                balances.append(balance)

        return balances

    async def save_balance(
        self,
        service: str,
        assets: list[AssetSchema],
        total_usd: float,
        actual: bool = True,
        accounts: list[AccountBalanceSchema] = None,
    ) -> Balance:
        existing = await self.get_latest_balance(service)

        assets_data = [asset.model_dump() for asset in assets]
        accounts_data = [acc.model_dump() for acc in accounts] if accounts else []

        if existing:
            existing_total = existing.total_usd
            has_changed = abs(existing_total - total_usd) > 0.01

            if not has_changed and existing.assets:
                existing_assets_set = {
                    (a.get("coin"), round(a.get("amount", 0), 8))
                    for a in existing.assets
                }
                new_assets_set = {
                    (a["coin"], round(a["amount"], 8)) for a in assets_data
                }
                has_changed = existing_assets_set != new_assets_set

            if has_changed:
                if total_usd > 0 or (total_usd == 0 and existing_total == 0):
                    history = BalanceHistory(
                        service=service,
                        assets=assets_data,
                        accounts=accounts_data,
                        total_usd=total_usd,
                    )
                    self.session.add(history)

                    existing.assets = assets_data
                    existing.accounts = accounts_data
                    existing.total_usd = total_usd
                    existing.actual = actual
                    existing.updated_at = datetime.now(timezone.utc)
                    await self.session.commit()
                    return existing
                else:
                    existing.actual = False
                    existing.updated_at = datetime.now(timezone.utc)
                    await self.session.commit()
                    return existing
            else:
                existing.actual = actual
                existing.accounts = (
                    accounts_data  # Update accounts even if total unchanged
                )
                existing.updated_at = datetime.now(timezone.utc)
                await self.session.commit()
                return existing
        else:
            now = datetime.now(timezone.utc)
            balance = Balance(
                service=service,
                assets=assets_data,
                accounts=accounts_data,
                total_usd=total_usd,
                actual=actual,
                updated_at=now,
            )
            self.session.add(balance)

            history = BalanceHistory(
                service=service,
                assets=assets_data,
                accounts=accounts_data,
                total_usd=total_usd,
                created_at=now,
            )
            self.session.add(history)

            await self.session.commit()
            return balance

    async def mark_as_stale(self, service: str) -> Optional[Balance]:
        balance = await self.get_latest_balance(service)
        if balance:
            balance.actual = False
            balance.updated_at = datetime.now(timezone.utc)
            await self.session.commit()
        return balance

    async def get_history(
        self,
        service: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[BalanceHistory]:
        query = select(BalanceHistory)

        if service:
            query = query.where(BalanceHistory.service == service)
        if start_date:
            query = query.where(BalanceHistory.created_at >= start_date)
        if end_date:
            query = query.where(BalanceHistory.created_at <= end_date)

        query = query.order_by(desc(BalanceHistory.created_at)).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_history_count(
        self,
        service: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> int:
        from sqlalchemy import func

        query = select(func.count(BalanceHistory.id))

        if service:
            query = query.where(BalanceHistory.service == service)
        if start_date:
            query = query.where(BalanceHistory.created_at >= start_date)
        if end_date:
            query = query.where(BalanceHistory.created_at <= end_date)

        result = await self.session.execute(query)
        return result.scalar() or 0


class ServiceStatusRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_status(self, service: str) -> Optional[ServiceStatus]:
        query = select(ServiceStatus).where(ServiceStatus.service == service)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_all_statuses(self) -> list[ServiceStatus]:
        query = select(ServiceStatus).order_by(ServiceStatus.service)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_status(
        self, service: str, is_healthy: bool, last_error: Optional[str] = None
    ) -> ServiceStatus:
        status = await self.get_status(service)

        if status:
            status.is_healthy = is_healthy
            status.last_error = last_error
            status.last_check = datetime.now(timezone.utc)
        else:
            status = ServiceStatus(
                service=service,
                is_healthy=is_healthy,
                last_error=last_error,
            )
            self.session.add(status)

        await self.session.commit()
        return status


class TransactionRepository:
    """Репозиторий для работы с транзакциями (вводы/выводы)"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_tx_id(self, service: str, tx_id: str) -> Optional[Transaction]:
        """Получить транзакцию по tx_id и сервису"""
        query = select(Transaction).where(
            and_(Transaction.service == service, Transaction.tx_id == tx_id)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_transactions(
        self,
        service: Optional[str] = None,
        tx_type: Optional[str] = None,  # deposit, withdrawal
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Transaction]:
        """Получить список транзакций с фильтрами"""
        query = select(Transaction)

        if service:
            query = query.where(Transaction.service == service)
        if tx_type:
            query = query.where(Transaction.tx_type == tx_type)
        if status:
            query = query.where(Transaction.status == status)
        if start_date:
            query = query.where(Transaction.tx_timestamp >= start_date)
        if end_date:
            query = query.where(Transaction.tx_timestamp <= end_date)

        query = (
            query.order_by(desc(Transaction.tx_timestamp)).offset(offset).limit(limit)
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_transaction_count(
        self,
        service: Optional[str] = None,
        tx_type: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> int:
        """Подсчёт транзакций с фильтрами"""
        from sqlalchemy import func

        query = select(func.count(Transaction.id))

        if service:
            query = query.where(Transaction.service == service)
        if tx_type:
            query = query.where(Transaction.tx_type == tx_type)
        if status:
            query = query.where(Transaction.status == status)
        if start_date:
            query = query.where(Transaction.tx_timestamp >= start_date)
        if end_date:
            query = query.where(Transaction.tx_timestamp <= end_date)

        result = await self.session.execute(query)
        return result.scalar() or 0

    async def get_unnotified_transactions(
        self, status: str = "ok"
    ) -> list[Transaction]:
        """Получить транзакции без отправленных уведомлений"""
        query = (
            select(Transaction)
            .where(and_(Transaction.notified == False, Transaction.status == status))
            .order_by(Transaction.tx_timestamp)
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def save_transaction(
        self, tx_data: TransactionSchema
    ) -> tuple[Transaction, bool]:
        """
        Сохранить или обновить транзакцию.
        Возвращает (transaction, is_new) - транзакцию и флаг, новая ли она.
        """
        existing = await self.get_by_tx_id(tx_data.service, tx_data.tx_id)

        if existing:
            # Обновляем существующую транзакцию
            status_changed = existing.status != tx_data.status

            existing.status = tx_data.status
            existing.txid = tx_data.txid or existing.txid
            existing.fee = tx_data.fee or existing.fee
            existing.fee_currency = tx_data.fee_currency or existing.fee_currency
            existing.network = tx_data.network or existing.network
            existing.address = tx_data.address or existing.address
            existing.address_from = tx_data.address_from or existing.address_from
            existing.address_to = tx_data.address_to or existing.address_to

            await self.session.commit()
            return existing, False
        else:
            # Создаём новую транзакцию
            transaction = Transaction(
                tx_id=tx_data.tx_id,
                service=tx_data.service,
                tx_type=tx_data.tx_type,
                currency=tx_data.currency,
                amount=tx_data.amount,
                fee=tx_data.fee,
                fee_currency=tx_data.fee_currency,
                network=tx_data.network,
                address=tx_data.address,
                address_from=tx_data.address_from,
                address_to=tx_data.address_to,
                tag=tx_data.tag,
                status=tx_data.status,
                txid=tx_data.txid,
                tx_timestamp=tx_data.tx_timestamp,
                notified=False,
            )
            self.session.add(transaction)
            await self.session.commit()
            return transaction, True

    async def mark_as_notified(self, transaction_id: int) -> bool:
        """Отметить транзакцию как обработанную (уведомление отправлено)"""
        query = select(Transaction).where(Transaction.id == transaction_id)
        result = await self.session.execute(query)
        transaction = result.scalar_one_or_none()

        if transaction:
            transaction.notified = True
            await self.session.commit()
            return True
        return False

    async def mark_multiple_as_notified(self, transaction_ids: list[int]) -> int:
        """Отметить несколько транзакций как обработанные"""
        if not transaction_ids:
            return 0

        from sqlalchemy import update

        stmt = (
            update(Transaction)
            .where(Transaction.id.in_(transaction_ids))
            .values(notified=True)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount

    async def get_last_transaction_timestamp(
        self, service: str, tx_type: str
    ) -> Optional[datetime]:
        """Получить время последней транзакции для сервиса"""
        query = (
            select(Transaction.tx_timestamp)
            .where(and_(Transaction.service == service, Transaction.tx_type == tx_type))
            .order_by(desc(Transaction.tx_timestamp))
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
