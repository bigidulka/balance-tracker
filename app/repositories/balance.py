import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import (
    DEFAULT_ORGANIZATION_ID,
    Balance,
    BalanceHistory,
    ServiceStatus,
    Transaction,
)
from app.schemas.balance import (
    AccountBalanceSchema,
    AssetSchema,
    TransactionSchema,
)

logger = logging.getLogger(__name__)


class BalanceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_latest_balance(
        self, service: str, organization_id: int = DEFAULT_ORGANIZATION_ID
    ) -> Optional[Balance]:
        query = (
            select(Balance)
            .where(
                and_(
                    Balance.organization_id == organization_id,
                    Balance.service == service,
                )
            )
            .order_by(desc(Balance.updated_at), desc(Balance.id))
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_all_latest_balances(
        self, organization_id: int = DEFAULT_ORGANIZATION_ID
    ) -> list[Balance]:
        ranked = (
            select(
                Balance.id.label("id"),
                func.row_number()
                .over(
                    partition_by=Balance.service,
                    order_by=(Balance.updated_at.desc(), Balance.id.desc()),
                )
                .label("rn"),
            )
            .where(Balance.organization_id == organization_id)
            .subquery()
        )

        query = (
            select(Balance)
            .join(ranked, Balance.id == ranked.c.id)
            .where(ranked.c.rn == 1)
            .order_by(Balance.service)
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def save_balance(
        self,
        service: str,
        assets: list[AssetSchema],
        total_usd: float,
        actual: bool = True,
        accounts: Optional[list[AccountBalanceSchema]] = None,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
    ) -> Balance:
        existing = await self.get_latest_balance(service, organization_id)

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
                new_assets_set = {(a["coin"], round(a["amount"], 8)) for a in assets_data}
                has_changed = existing_assets_set != new_assets_set

            if has_changed:
                if total_usd > 0 or (total_usd == 0 and existing_total == 0):
                    history = BalanceHistory(
                        organization_id=organization_id,
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

                existing.actual = False
                existing.updated_at = datetime.now(timezone.utc)
                await self.session.commit()
                return existing

            existing.actual = actual
            existing.accounts = accounts_data
            existing.updated_at = datetime.now(timezone.utc)
            await self.session.commit()
            return existing

        now = datetime.now(timezone.utc)
        balance = Balance(
            organization_id=organization_id,
            service=service,
            assets=assets_data,
            accounts=accounts_data,
            total_usd=total_usd,
            actual=actual,
            updated_at=now,
        )
        self.session.add(balance)

        history = BalanceHistory(
            organization_id=organization_id,
            service=service,
            assets=assets_data,
            accounts=accounts_data,
            total_usd=total_usd,
            created_at=now,
        )
        self.session.add(history)

        await self.session.commit()
        return balance

    async def mark_as_stale(
        self, service: str, organization_id: int = DEFAULT_ORGANIZATION_ID
    ) -> Optional[Balance]:
        balance = await self.get_latest_balance(service, organization_id)
        if balance:
            balance.actual = False
            balance.updated_at = datetime.now(timezone.utc)
            await self.session.commit()
        return balance

    async def get_history(
        self,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        service: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[BalanceHistory]:
        query = select(BalanceHistory).where(
            BalanceHistory.organization_id == organization_id
        )

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
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        service: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> int:
        from sqlalchemy import func

        query = select(func.count(BalanceHistory.id)).where(
            BalanceHistory.organization_id == organization_id
        )

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

    async def get_status(
        self, service: str, organization_id: int = DEFAULT_ORGANIZATION_ID
    ) -> Optional[ServiceStatus]:
        query = select(ServiceStatus).where(
            and_(
                ServiceStatus.organization_id == organization_id,
                ServiceStatus.service == service,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_all_statuses(
        self, organization_id: int = DEFAULT_ORGANIZATION_ID
    ) -> list[ServiceStatus]:
        query = (
            select(ServiceStatus)
            .where(ServiceStatus.organization_id == organization_id)
            .order_by(ServiceStatus.service)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_status(
        self,
        service: str,
        is_healthy: bool,
        last_error: Optional[str] = None,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
    ) -> ServiceStatus:
        status = await self.get_status(service, organization_id)

        if status:
            status.is_healthy = is_healthy
            status.last_error = last_error
            status.last_check = datetime.now(timezone.utc)
        else:
            status = ServiceStatus(
                organization_id=organization_id,
                service=service,
                is_healthy=is_healthy,
                last_error=last_error,
            )
            self.session.add(status)

        await self.session.commit()
        return status


class TransactionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_tx_id(
        self,
        service: str,
        tx_id: str,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
    ) -> Optional[Transaction]:
        query = select(Transaction).where(
            and_(
                Transaction.organization_id == organization_id,
                Transaction.service == service,
                Transaction.tx_id == tx_id,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_transactions(
        self,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        service: Optional[str] = None,
        tx_type: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Transaction]:
        query = select(Transaction).where(Transaction.organization_id == organization_id)

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

        query = query.order_by(desc(Transaction.tx_timestamp)).offset(offset).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_transaction_count(
        self,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        service: Optional[str] = None,
        tx_type: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> int:
        from sqlalchemy import func

        query = select(func.count(Transaction.id)).where(
            Transaction.organization_id == organization_id
        )

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
        self,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        status: str = "ok",
    ) -> list[Transaction]:
        query = (
            select(Transaction)
            .where(
                and_(
                    Transaction.organization_id == organization_id,
                    Transaction.notified == False,
                    Transaction.status == status,
                )
            )
            .order_by(Transaction.tx_timestamp)
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    def _apply_transaction_updates(self, existing: Transaction, tx_data: TransactionSchema) -> bool:
        changed = False

        if existing.status != tx_data.status:
            existing.status = tx_data.status
            changed = True

        if tx_data.txid and existing.txid != tx_data.txid:
            existing.txid = tx_data.txid
            changed = True

        if tx_data.fee is not None and existing.fee != tx_data.fee:
            existing.fee = tx_data.fee
            changed = True

        if tx_data.fee_currency and existing.fee_currency != tx_data.fee_currency:
            existing.fee_currency = tx_data.fee_currency
            changed = True

        if tx_data.network and existing.network != tx_data.network:
            existing.network = tx_data.network
            changed = True

        if tx_data.address and existing.address != tx_data.address:
            existing.address = tx_data.address
            changed = True

        if tx_data.address_from and existing.address_from != tx_data.address_from:
            existing.address_from = tx_data.address_from
            changed = True

        if tx_data.address_to and existing.address_to != tx_data.address_to:
            existing.address_to = tx_data.address_to
            changed = True

        return changed

    async def save_transaction(
        self,
        tx_data: TransactionSchema,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
    ) -> tuple[Transaction, bool]:
        existing = await self.get_by_tx_id(
            tx_data.service, tx_data.tx_id, organization_id=organization_id
        )

        if existing:
            changed = self._apply_transaction_updates(existing, tx_data)
            if changed:
                await self.session.commit()
            return existing, False

        transaction = Transaction(
            organization_id=organization_id,
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

        try:
            await self.session.commit()
            return transaction, True
        except IntegrityError:
            await self.session.rollback()
            existing_after_conflict = await self.get_by_tx_id(
                tx_data.service, tx_data.tx_id, organization_id=organization_id
            )
            if existing_after_conflict is None:
                raise
            return existing_after_conflict, False

    async def save_transactions_batch(
        self,
        transactions: list[TransactionSchema],
        organization_id: int = DEFAULT_ORGANIZATION_ID,
    ) -> tuple[int, int]:
        new_count = 0
        updated_count = 0

        for tx_data in transactions:
            existing = await self.get_by_tx_id(
                tx_data.service, tx_data.tx_id, organization_id=organization_id
            )

            if existing:
                self._apply_transaction_updates(existing, tx_data)
                updated_count += 1
                continue

            transaction = Transaction(
                organization_id=organization_id,
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
            new_count += 1

        try:
            await self.session.commit()
            return new_count, updated_count
        except IntegrityError:
            await self.session.rollback()
            fallback_new = 0
            fallback_updated = 0
            for tx_data in transactions:
                _, is_new = await self.save_transaction(
                    tx_data, organization_id=organization_id
                )
                if is_new:
                    fallback_new += 1
                else:
                    fallback_updated += 1
            return fallback_new, fallback_updated

    async def mark_as_notified(
        self,
        transaction_id: int,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
    ) -> bool:
        query = select(Transaction).where(
            and_(
                Transaction.organization_id == organization_id,
                Transaction.id == transaction_id,
            )
        )
        result = await self.session.execute(query)
        transaction = result.scalar_one_or_none()

        if transaction:
            transaction.notified = True
            await self.session.commit()
            return True
        return False

    async def mark_multiple_as_notified(
        self,
        transaction_ids: list[int],
        organization_id: int = DEFAULT_ORGANIZATION_ID,
    ) -> int:
        if not transaction_ids:
            return 0

        from sqlalchemy import update

        stmt = (
            update(Transaction)
            .where(
                and_(
                    Transaction.organization_id == organization_id,
                    Transaction.id.in_(transaction_ids),
                )
            )
            .values(notified=True)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount

    async def get_last_transaction_timestamp(
        self,
        service: str,
        tx_type: str,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
    ) -> Optional[datetime]:
        query = (
            select(Transaction.tx_timestamp)
            .where(
                and_(
                    Transaction.organization_id == organization_id,
                    Transaction.service == service,
                    Transaction.tx_type == tx_type,
                )
            )
            .order_by(desc(Transaction.tx_timestamp))
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
