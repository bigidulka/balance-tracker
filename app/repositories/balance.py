import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import Balance, BalanceHistory, ServiceStatus
from app.schemas.balance import AccountBalanceSchema, AssetSchema, ServiceBalanceSchema

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
                    await self.session.commit()
                    return existing
                else:
                    existing.actual = False
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
            balance = Balance(
                service=service,
                assets=assets_data,
                accounts=accounts_data,
                total_usd=total_usd,
                actual=actual,
            )
            self.session.add(balance)

            history = BalanceHistory(
                service=service,
                assets=assets_data,
                accounts=accounts_data,
                total_usd=total_usd,
            )
            self.session.add(history)

            await self.session.commit()
            return balance

    async def mark_as_stale(self, service: str) -> Optional[Balance]:
        balance = await self.get_latest_balance(service)
        if balance:
            balance.actual = False
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
