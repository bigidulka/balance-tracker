import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.repositories.balance import BalanceRepository, ServiceStatusRepository
from app.schemas.balance import (
    AccountBalanceSchema,
    AssetSchema,
    PortfolioResponse,
    ServiceBalanceSchema,
)
from app.services.ccxt_manager import ccxt_manager
from app.services.okx_wallet import okx_wallet_service

logger = logging.getLogger(__name__)
settings = get_settings()

_balance_cache: dict[str, tuple[ServiceBalanceSchema, float]] = {}


class BalanceService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.balance_repo = BalanceRepository(session)
        self.status_repo = ServiceStatusRepository(session)

    def _get_cached_balance(self, service: str) -> Optional[ServiceBalanceSchema]:
        import time

        if service in _balance_cache:
            balance, cached_at = _balance_cache[service]
            if time.time() - cached_at < settings.balance_cache_ttl:
                return balance
            del _balance_cache[service]
        return None

    def _set_cached_balance(self, service: str, balance: ServiceBalanceSchema):
        import time

        _balance_cache[service] = (balance, time.time())

    async def _get_fallback_balance(
        self, service: str
    ) -> Optional[ServiceBalanceSchema]:
        db_balance = await self.balance_repo.get_latest_balance(service)
        if db_balance and db_balance.total_usd > 0:
            assets = [AssetSchema(**a) for a in db_balance.assets]
            accounts = []
            if db_balance.accounts:
                for acc in db_balance.accounts:
                    acc_assets = [AssetSchema(**a) for a in acc.get("assets", [])]
                    accounts.append(
                        AccountBalanceSchema(
                            account_type=acc.get("account_type", "spot"),
                            assets=acc_assets,
                            total_usd=acc.get("total_usd", 0),
                        )
                    )
            return ServiceBalanceSchema(
                service=service,
                accounts=accounts,
                assets=assets,
                total_usd=db_balance.total_usd,
                updated_at=db_balance.updated_at,
                actual=False,
            )
        return None

    async def fetch_and_save_balance(
        self, service: str, balance: ServiceBalanceSchema
    ) -> ServiceBalanceSchema:
        await self.balance_repo.save_balance(
            service=service,
            assets=balance.assets,
            total_usd=balance.total_usd,
            actual=balance.actual,
            accounts=balance.accounts,
        )
        await self.status_repo.update_status(service, is_healthy=True)
        self._set_cached_balance(service, balance)
        return balance

    async def handle_fetch_error(
        self, service: str, error: Exception
    ) -> Optional[ServiceBalanceSchema]:
        await self.status_repo.update_status(
            service, is_healthy=False, last_error=str(error)
        )

        fallback = await self._get_fallback_balance(service)
        if fallback:
            await self.balance_repo.mark_as_stale(service)
            return fallback

        return None

    async def get_all_balances(self, force_refresh: bool = False) -> PortfolioResponse:
        exchange_ids = settings.get_active_exchanges()
        okx_wallet_ids = settings.okx_wallet_accounts

        all_balances: list[ServiceBalanceSchema] = []

        if not force_refresh:
            for service in exchange_ids:
                cached = self._get_cached_balance(service)
                if cached:
                    all_balances.append(cached)
                    exchange_ids = [e for e in exchange_ids if e != service]

            for account_id in okx_wallet_ids[:]:
                service_name = f"okx_wallet_{account_id[:8]}"
                cached = self._get_cached_balance(service_name)
                if cached:
                    all_balances.append(cached)
                    okx_wallet_ids = [a for a in okx_wallet_ids if a != account_id]

        tasks = []
        task_map = {}

        for exchange_id in exchange_ids:
            task = asyncio.create_task(ccxt_manager.fetch_balance(exchange_id))
            tasks.append(task)
            task_map[id(task)] = ("exchange", exchange_id)

        for account_id in okx_wallet_ids:
            task = asyncio.create_task(
                okx_wallet_service.fetch_wallet_balance(account_id)
            )
            tasks.append(task)
            task_map[id(task)] = ("okx_wallet", account_id)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for task, result in zip(tasks, results):
                source_type, source_id = task_map[id(task)]

                if source_type == "exchange":
                    service = source_id
                else:
                    service = f"okx_wallet_{source_id[:8]}"

                if isinstance(result, BaseException):
                    logger.warning(f"Failed to fetch {service}: {result}")
                    fallback = await self.handle_fetch_error(service, result)
                    if fallback:
                        all_balances.append(fallback)
                elif result is not None:
                    saved = await self.fetch_and_save_balance(service, result)
                    all_balances.append(saved)

        total_usd = sum(b.total_usd for b in all_balances)

        return PortfolioResponse(
            total_usd=total_usd,
            services=all_balances,
            timestamp=datetime.now(timezone.utc),
        )

    async def refresh_all(self) -> tuple[list[str], list[str]]:
        updated = []
        failed = []

        exchange_ids = settings.get_active_exchanges()
        okx_wallet_ids = settings.okx_wallet_accounts

        exchange_results = await ccxt_manager.fetch_all_balances(exchange_ids)

        for exchange_id, result in exchange_results.items():
            if isinstance(result, Exception):
                await self.handle_fetch_error(exchange_id, result)
                failed.append(exchange_id)
            else:
                await self.fetch_and_save_balance(exchange_id, result)
                updated.append(exchange_id)

        wallet_results = await okx_wallet_service.fetch_all_wallets(okx_wallet_ids)

        for service_name, result in wallet_results.items():
            if isinstance(result, Exception):
                await self.handle_fetch_error(service_name, result)
                failed.append(service_name)
            else:
                await self.fetch_and_save_balance(service_name, result)
                updated.append(service_name)

        return updated, failed
