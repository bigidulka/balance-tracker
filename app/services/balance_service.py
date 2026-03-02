import asyncio
import logging
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.balance import SyncJob
from app.repositories.balance import BalanceRepository, ServiceStatusRepository
from app.services.entitlements_service import EntitlementsService
from app.schemas.balance import (
    AccountBalanceSchema,
    AssetSchema,
    PortfolioResponse,
    ServiceBalanceSchema,
)
from app.services.ccxt_manager import ccxt_manager
from app.services.okx_wallet import okx_wallet_service
from app.services.metrics_service import metrics_service

logger = logging.getLogger(__name__)
settings = get_settings()

_balance_cache: dict[tuple[int, str], dict[str, object]] = {}
_balance_cache_lock = Lock()


class BalanceService:
    def __init__(self, session: AsyncSession, organization_id: int = 1):
        self.session = session
        self.organization_id = organization_id
        self.balance_repo = BalanceRepository(session)
        self.status_repo = ServiceStatusRepository(session)
        self.entitlements = EntitlementsService(session)

    def _get_cached_balance(self, service: str) -> Optional[ServiceBalanceSchema]:
        state = self._get_cached_balance_state(service)
        return state["balance"] if state else None

    def _get_cached_balance_state(self, service: str) -> Optional[dict[str, object]]:
        cache_key = (self.organization_id, service)

        state: Optional[str] = None
        cached_at = 0.0
        balance: Optional[ServiceBalanceSchema] = None

        with _balance_cache_lock:
            entry = _balance_cache.get(cache_key)
            if not entry:
                return None

            cached_at = float(entry.get("cached_at", 0.0))
            age = time.time() - cached_at
            soft_ttl = max(1, settings.balance_cache_ttl)
            hard_ttl = max(soft_ttl, settings.balance_cache_hard_ttl)

            if age <= soft_ttl:
                state = "fresh"
                balance = entry.get("balance")
            elif age <= hard_ttl:
                state = "stale"
                balance = entry.get("balance")
            else:
                _balance_cache.pop(cache_key, None)
                return None

        if state == "fresh":
            metrics_service.inc("ccxt_cache_l1_hit_total")
        elif state == "stale":
            metrics_service.inc("ccxt_cache_l1_stale_served_total")

        return {
            "balance": balance,
            "cached_at": cached_at,
            "state": state,
        }

    def _set_cached_balance(self, service: str, balance: ServiceBalanceSchema):
        cache_key = (self.organization_id, service)
        with _balance_cache_lock:
            _balance_cache[cache_key] = {
                "balance": balance,
                "cached_at": time.time(),
                "state": "fresh",
            }

    async def _get_fallback_balance(
        self, service: str
    ) -> Optional[ServiceBalanceSchema]:
        db_balance = await self.balance_repo.get_latest_balance(service, self.organization_id)
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
            organization_id=self.organization_id,
        )
        await self.status_repo.update_status(
            service, is_healthy=True, organization_id=self.organization_id
        )
        self._set_cached_balance(service, balance)
        return balance

    async def handle_fetch_error(
        self, service: str, error: Exception
    ) -> Optional[ServiceBalanceSchema]:
        await self.status_repo.update_status(
            service,
            is_healthy=False,
            last_error=str(error),
            organization_id=self.organization_id,
        )

        fallback = await self._get_fallback_balance(service)
        if fallback:
            await self.balance_repo.mark_as_stale(service, self.organization_id)
            return fallback

        return None

    async def get_all_balances(self, force_refresh: bool = False) -> PortfolioResponse:
        exchange_ids = settings.get_active_exchanges()
        okx_wallet_ids = settings.okx_wallet_accounts

        all_balances_by_service: dict[str, ServiceBalanceSchema] = {}

        stale_revalidate_services: set[str] = set()

        if not force_refresh:
            fetch_exchanges: list[str] = []
            revalidate_exchanges: list[str] = []
            for service in exchange_ids:
                cached_state = self._get_cached_balance_state(service)
                if not cached_state:
                    metrics_service.inc("ccxt_cache_miss_total")
                    fetch_exchanges.append(service)
                    continue

                cached_balance = cached_state.get("balance")
                cache_state = str(cached_state.get("state"))

                if cache_state == "stale":
                    if settings.enable_ccxt_stale_revalidate:
                        if cached_balance:
                            all_balances_by_service[service] = cached_balance
                        stale_revalidate_services.add(service)
                        revalidate_exchanges.append(service)
                    else:
                        metrics_service.inc("ccxt_cache_miss_total")
                        fetch_exchanges.append(service)
                    continue

                if cached_balance:
                    all_balances_by_service[service] = cached_balance

            exchange_ids = fetch_exchanges + revalidate_exchanges

            fetch_wallet_ids: list[str] = []
            revalidate_wallet_ids: list[str] = []
            for account_id in okx_wallet_ids:
                service_name = f"okx_wallet_{account_id[:8]}"
                cached_state = self._get_cached_balance_state(service_name)
                if not cached_state:
                    metrics_service.inc("ccxt_cache_miss_total")
                    fetch_wallet_ids.append(account_id)
                    continue

                cached_balance = cached_state.get("balance")
                cache_state = str(cached_state.get("state"))

                if cache_state == "stale":
                    if settings.enable_ccxt_stale_revalidate:
                        if cached_balance:
                            all_balances_by_service[service_name] = cached_balance
                        stale_revalidate_services.add(service_name)
                        revalidate_wallet_ids.append(account_id)
                    else:
                        metrics_service.inc("ccxt_cache_miss_total")
                        fetch_wallet_ids.append(account_id)
                    continue

                if cached_balance:
                    all_balances_by_service[service_name] = cached_balance

            okx_wallet_ids = fetch_wallet_ids + revalidate_wallet_ids

        tasks = []
        task_map = {}
        parallelism = max(1, settings.exchange_parallelism)
        semaphore = asyncio.Semaphore(parallelism)

        async def _run_with_limit(coro):
            async with semaphore:
                return await coro

        for exchange_id in exchange_ids:
            task = asyncio.create_task(_run_with_limit(ccxt_manager.fetch_balance(exchange_id)))
            tasks.append(task)
            task_map[id(task)] = ("exchange", exchange_id)

        for account_id in okx_wallet_ids:
            task = asyncio.create_task(
                _run_with_limit(okx_wallet_service.fetch_wallet_balance(account_id))
            )
            tasks.append(task)
            task_map[id(task)] = ("okx_wallet", account_id)

        if tasks:
            logger.info(f"Gathering balances from {len(tasks)} sources...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Gathering balances completed")

            for task, result in zip(tasks, results):
                source_type, source_id = task_map[id(task)]

                if source_type == "exchange":
                    service = source_id
                else:
                    service = f"okx_wallet_{source_id[:8]}"

                if isinstance(result, BaseException):
                    logger.warning(f"Failed to fetch {service}: {result}")
                    fallback = await self.handle_fetch_error(service, result)
                    if fallback and service not in stale_revalidate_services:
                        all_balances_by_service[service] = fallback
                elif result is not None:
                    saved = await self.fetch_and_save_balance(service, result)
                    all_balances_by_service[service] = saved

        all_balances = list(all_balances_by_service.values())
        total_usd = sum(b.total_usd for b in all_balances)

        return PortfolioResponse(
            total_usd=total_usd,
            services=all_balances,
            timestamp=datetime.now(timezone.utc),
        )

    async def refresh_all(self) -> tuple[list[str], list[str]]:
        updated = []
        failed = []

        await self.entitlements.ensure_refresh_interval_for_organization(self.organization_id)

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

        if failed:
            job_status = "partial" if updated else "failed"
        else:
            job_status = "success"

        job = SyncJob(
            organization_id=self.organization_id,
            job_type="refresh",
            status=job_status,
            payload={"exchange_count": len(exchange_ids), "wallet_count": len(okx_wallet_ids)},
            result={"updated": updated, "failed": failed},
            finished_at=datetime.now(timezone.utc),
        )
        self.session.add(job)
        await self.session.commit()

        return updated, failed
