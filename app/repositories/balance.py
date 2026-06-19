import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import and_, desc, func, select, text
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
        self,
        service: str,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        integration_id: int | None = None,
    ) -> Optional[Balance]:
        predicates = [
            Balance.organization_id == organization_id,
            Balance.service == service,
        ]
        if integration_id is not None:
            predicates.append(Balance.integration_id == integration_id)
        query = select(Balance).where(and_(*predicates)).order_by(desc(Balance.updated_at), desc(Balance.id)).limit(1)
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
                    partition_by=(func.coalesce(Balance.integration_id, -1), Balance.service),
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
        integration_id: int | None = None,
    ) -> Balance:
        existing = await self.get_latest_balance(
            service,
            organization_id,
            integration_id=integration_id,
        )

        assets_data = [asset.model_dump() for asset in assets]
        accounts_data = [acc.model_dump() for acc in accounts] if accounts else []

        def _normalized_accounts(rows: list[dict]) -> tuple:
            normalized: list[tuple] = []
            for row in rows:
                assets_key = tuple(
                    sorted(
                        (
                            str(asset.get("coin") or ""),
                            round(float(asset.get("amount", 0) or 0), 8),
                            round(float(asset.get("value_usd", 0) or 0), 8),
                        )
                        for asset in (row.get("assets") or [])
                    )
                )
                normalized.append(
                    (
                        str(row.get("account_type") or "spot"),
                        round(float(row.get("total_usd", 0) or 0), 8),
                        assets_key,
                    )
                )
            return tuple(sorted(normalized))

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

            if not has_changed:
                has_changed = _normalized_accounts(existing.accounts or []) != _normalized_accounts(
                    accounts_data
                )

            if has_changed:
                now = datetime.now(timezone.utc)
                history = BalanceHistory(
                    organization_id=organization_id,
                    integration_id=integration_id,
                    service=service,
                    assets=assets_data,
                    accounts=accounts_data,
                    total_usd=total_usd,
                    actual=actual,
                    created_at=now,
                )
                self.session.add(history)

                existing.assets = assets_data
                existing.accounts = accounts_data
                existing.total_usd = total_usd
                existing.actual = actual
                existing.updated_at = now
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
            integration_id=integration_id,
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
            integration_id=integration_id,
            service=service,
            assets=assets_data,
            accounts=accounts_data,
            total_usd=total_usd,
            actual=actual,
            created_at=now,
        )
        self.session.add(history)

        await self.session.commit()
        return balance

    async def mark_as_stale(
        self,
        service: str,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        integration_id: int | None = None,
    ) -> Optional[Balance]:
        balance = await self.get_latest_balance(
            service,
            organization_id,
            integration_id=integration_id,
        )
        if balance:
            balance.actual = False
            balance.updated_at = datetime.now(timezone.utc)
            await self.session.commit()
        return balance

    async def get_history(
        self,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        service: Optional[str] = None,
        integration_id: int | None = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        order: str = "desc",
    ) -> list[BalanceHistory]:
        query = select(BalanceHistory).where(
            BalanceHistory.organization_id == organization_id
        )

        if service:
            query = query.where(BalanceHistory.service == service)
        if integration_id is not None:
            query = query.where(BalanceHistory.integration_id == integration_id)
        if start_date:
            query = query.where(BalanceHistory.created_at >= start_date)
        if end_date:
            query = query.where(BalanceHistory.created_at <= end_date)

        if order == "asc":
            query = query.order_by(BalanceHistory.created_at.asc(), BalanceHistory.id.asc())
        else:
            query = query.order_by(desc(BalanceHistory.created_at), desc(BalanceHistory.id))
        query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_history_points(
        self,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        service: Optional[str] = None,
        integration_id: int | None = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int | None = None,
    ) -> list[BalanceHistory]:
        query = select(BalanceHistory).where(
            BalanceHistory.organization_id == organization_id
        )

        if service:
            query = query.where(BalanceHistory.service == service)
        if integration_id is not None:
            query = query.where(BalanceHistory.integration_id == integration_id)
        if start_date:
            query = query.where(BalanceHistory.created_at >= start_date)
        if end_date:
            query = query.where(BalanceHistory.created_at <= end_date)

        query = query.order_by(BalanceHistory.created_at.asc(), BalanceHistory.id.asc())
        if limit is not None:
            query = query.limit(max(1, int(limit)))
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_portfolio_snapshot_total_at(
        self,
        organization_id: int,
        at: datetime,
        *,
        max_age: timedelta | None = None,
    ) -> float:
        """Return portfolio total at a point in time.

        BalanceHistory stores one row per service/integration, not one row per
        portfolio snapshot. This method builds a point-in-time portfolio by
        taking the latest history row for each service/integration at or before
        `at`, then summing those rows.
        """
        row_number = func.row_number().over(
            partition_by=(BalanceHistory.service, BalanceHistory.integration_id),
            order_by=(BalanceHistory.created_at.desc(), BalanceHistory.id.desc()),
        ).label("rn")
        predicates = [
            BalanceHistory.organization_id == organization_id,
            BalanceHistory.created_at <= at,
        ]
        if max_age is not None:
            predicates.append(BalanceHistory.created_at >= at - max_age)

        subquery = (
            select(
                BalanceHistory.total_usd.label("total_usd"),
                row_number,
            )
            .where(and_(*predicates))
            .subquery()
        )
        result = await self.session.execute(
            select(func.coalesce(func.sum(subquery.c.total_usd), 0.0)).where(
                subquery.c.rn == 1
            )
        )
        return float(result.scalar_one() or 0.0)

    async def get_portfolio_snapshot_total_for_keys(
        self,
        organization_id: int,
        at: datetime,
        keys: Sequence[tuple[str, object]],
        *,
        max_age: timedelta | None = None,
    ) -> float:
        """Return point-in-time portfolio total for known active balance keys.

        This is optimized for dashboard PnL: the current portfolio already knows
        which integration/service keys are active, so use index seeks per key
        instead of window-scanning the full balance_history table.
        """
        total = 0.0
        seen: set[tuple[str, int | str]] = set()
        for key_type, raw_value in keys:
            key = (str(key_type), raw_value)
            if key in seen:
                continue
            seen.add(key)

            predicates = [
                BalanceHistory.organization_id == organization_id,
                BalanceHistory.created_at <= at,
            ]
            if max_age is not None:
                predicates.append(BalanceHistory.created_at >= at - max_age)

            if key_type == "service_integration":
                if not isinstance(raw_value, tuple) or len(raw_value) != 2:
                    continue
                service, integration_id = raw_value
                predicates.append(BalanceHistory.service == str(service))
                if integration_id is None:
                    predicates.append(BalanceHistory.integration_id.is_(None))
                else:
                    predicates.append(BalanceHistory.integration_id == int(integration_id))
            elif key_type == "integration":
                predicates.append(BalanceHistory.integration_id == int(raw_value))
            elif key_type == "service":
                predicates.extend(
                    [
                        BalanceHistory.service == str(raw_value),
                        BalanceHistory.integration_id.is_(None),
                    ]
                )
            else:
                continue

            result = await self.session.execute(
                select(BalanceHistory.total_usd)
                .where(and_(*predicates))
                .order_by(desc(BalanceHistory.created_at), desc(BalanceHistory.id))
                .limit(1)
            )
            total += float(result.scalar_one_or_none() or 0.0)
        return total

    async def get_portfolio_snapshot_totals_for_keys(
        self,
        organization_id: int,
        points: Sequence[datetime],
        keys: Sequence[tuple[str, object]],
        *,
        max_age: timedelta | None = None,
    ) -> list[float]:
        """Return point-in-time portfolio totals for multiple snapshot points."""
        if not points:
            return []

        bind = self.session.get_bind()
        if bind.dialect.name != "postgresql" or max_age is not None:
            return [
                await self.get_portfolio_snapshot_total_for_keys(
                    organization_id=organization_id,
                    at=point,
                    keys=keys,
                    max_age=max_age,
                )
                for point in points
            ]

        normalized_keys: list[tuple[str, int | None]] = []
        seen: set[tuple[str, int | None]] = set()
        for key_type, raw_value in keys:
            if key_type != "service_integration":
                return [
                    await self.get_portfolio_snapshot_total_for_keys(
                        organization_id=organization_id,
                        at=point,
                        keys=keys,
                        max_age=max_age,
                    )
                    for point in points
                ]
            if not isinstance(raw_value, tuple) or len(raw_value) != 2:
                continue
            service, integration_id = raw_value
            normalized = (
                str(service),
                None if integration_id is None else int(integration_id),
            )
            if normalized in seen:
                continue
            seen.add(normalized)
            normalized_keys.append(normalized)

        if not normalized_keys:
            return [0.0 for _ in points]

        params: dict[str, object] = {"organization_id": organization_id}
        point_selects: list[str] = []
        for idx, point in enumerate(points):
            params[f"point_{idx}"] = point
            point_selects.append(
                f"SELECT {idx} AS point_idx, "
                f"CAST(:point_{idx} AS TIMESTAMP WITH TIME ZONE) AS snapshot_at"
            )

        key_selects: list[str] = []
        for idx, (service, integration_id) in enumerate(normalized_keys):
            params[f"service_{idx}"] = service
            params[f"integration_{idx}"] = integration_id
            key_selects.append(
                f"SELECT {idx} AS key_idx, "
                f"CAST(:service_{idx} AS VARCHAR) AS service, "
                f"CAST(:integration_{idx} AS INTEGER) AS integration_id"
            )

        query = text(
            f"""
            WITH points AS (
                {" UNION ALL ".join(point_selects)}
            ),
            active_keys AS (
                {" UNION ALL ".join(key_selects)}
            )
            SELECT
                points.point_idx,
                COALESCE(SUM(latest.total_usd), 0.0) AS total_usd
            FROM points
            CROSS JOIN active_keys
            LEFT JOIN LATERAL (
                SELECT balance_history.total_usd
                FROM balance_history
                WHERE balance_history.organization_id = :organization_id
                  AND balance_history.created_at <= points.snapshot_at
                  AND balance_history.service = active_keys.service
                  AND (
                    (
                        active_keys.integration_id IS NULL
                        AND balance_history.integration_id IS NULL
                    )
                    OR balance_history.integration_id = active_keys.integration_id
                  )
                ORDER BY balance_history.created_at DESC, balance_history.id DESC
                LIMIT 1
            ) latest ON TRUE
            GROUP BY points.point_idx
            ORDER BY points.point_idx
            """
        )
        result = await self.session.execute(query, params)
        values = [0.0 for _ in points]
        for row in result:
            values[int(row.point_idx)] = float(row.total_usd or 0.0)
        return values

    async def get_portfolio_snapshot_total_near(
        self,
        organization_id: int,
        at: datetime,
        *,
        lookback: timedelta,
    ) -> float:
        """Return total portfolio snapshot near a point in time.

        This reconstructs a total portfolio snapshot from a short history
        window by taking the latest row per service/integration before `at`.
        It reflects the portfolio that existed at that time, not today's active
        integration list, and avoids scanning the full history table.
        """
        row_number = func.row_number().over(
            partition_by=(BalanceHistory.service, BalanceHistory.integration_id),
            order_by=(BalanceHistory.created_at.desc(), BalanceHistory.id.desc()),
        ).label("rn")
        subquery = (
            select(
                BalanceHistory.total_usd.label("total_usd"),
                row_number,
            )
            .where(
                and_(
                    BalanceHistory.organization_id == organization_id,
                    BalanceHistory.created_at <= at,
                    BalanceHistory.created_at >= at - lookback,
                )
            )
            .subquery()
        )
        result = await self.session.execute(
            select(func.coalesce(func.sum(subquery.c.total_usd), 0.0)).where(
                subquery.c.rn == 1
            )
        )
        return float(result.scalar_one() or 0.0)

    async def get_history_count(
        self,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        service: Optional[str] = None,
        integration_id: int | None = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> int:
        from sqlalchemy import func

        query = select(func.count(BalanceHistory.id)).where(
            BalanceHistory.organization_id == organization_id
        )

        if service:
            query = query.where(BalanceHistory.service == service)
        if integration_id is not None:
            query = query.where(BalanceHistory.integration_id == integration_id)
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
        integration_id: int | None = None,
    ) -> Optional[Transaction]:
        predicates = [
            Transaction.organization_id == organization_id,
            Transaction.service == service,
            Transaction.tx_id == tx_id,
        ]
        if integration_id is not None:
            predicates.append(Transaction.integration_id == integration_id)
        query = select(Transaction).where(and_(*predicates))
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_transactions(
        self,
        organization_id: int = DEFAULT_ORGANIZATION_ID,
        service: Optional[str] = None,
        integration_id: int | None = None,
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
        if integration_id is not None:
            query = query.where(Transaction.integration_id == integration_id)
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
        integration_id: int | None = None,
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
        if integration_id is not None:
            query = query.where(Transaction.integration_id == integration_id)
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
            tx_data.service,
            tx_data.tx_id,
            organization_id=organization_id,
            integration_id=tx_data.integration_id,
        )

        if existing:
            changed = self._apply_transaction_updates(existing, tx_data)
            if changed:
                await self.session.commit()
            return existing, False

        transaction = Transaction(
            organization_id=organization_id,
            integration_id=tx_data.integration_id,
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
                tx_data.service,
                tx_data.tx_id,
                organization_id=organization_id,
                integration_id=tx_data.integration_id,
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
                tx_data.service,
                tx_data.tx_id,
                organization_id=organization_id,
                integration_id=tx_data.integration_id,
            )

            if existing:
                self._apply_transaction_updates(existing, tx_data)
                updated_count += 1
                continue

            transaction = Transaction(
                organization_id=organization_id,
                integration_id=tx_data.integration_id,
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
        integration_id: int | None = None,
    ) -> Optional[datetime]:
        predicates = [
            Transaction.organization_id == organization_id,
            Transaction.service == service,
            Transaction.tx_type == tx_type,
        ]
        if integration_id is not None:
            predicates.append(Transaction.integration_id == integration_id)
        query = (
            select(Transaction.tx_timestamp)
            .where(and_(*predicates))
            .order_by(desc(Transaction.tx_timestamp))
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
