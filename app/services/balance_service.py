import asyncio
import logging
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional

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

_balance_cache: dict[tuple[int, str, int | None], dict[str, object]] = {}
_balance_cache_lock = Lock()


class BalanceService:
    def __init__(self, session: AsyncSession, organization_id: int = 1):
        self.session = session
        self.organization_id = organization_id
        self.balance_repo = BalanceRepository(session)
        self.status_repo = ServiceStatusRepository(session)
        self.entitlements = EntitlementsService(session)

    async def load_balance_targets(self) -> tuple[list[dict[str, Any]], set[str]]:
        from app.services.integration_service import IntegrationService

        integrations = await IntegrationService(self.session).list_integrations(
            self.organization_id,
            include_inactive=False,
        )

        targets: list[dict[str, Any]] = []
        allowed_services: set[str] = set()

        for integration in integrations:
            kind = str(integration.kind or "").strip().lower()
            if kind == "cex":
                exchange_code = str(integration.exchange_code or "").strip().lower()
                if exchange_code:
                    allowed_services.add(exchange_code)
                    targets.append(
                        {
                            "kind": "cex",
                            "integration_id": integration.id,
                            "service": exchange_code,
                            "source_id": exchange_code,
                        }
                    )
                continue

            if kind == "dex":
                wallet_address = str(integration.wallet_address or "").strip()
                if wallet_address:
                    service_name = okx_wallet_service.service_name_for(wallet_address)
                    allowed_services.add(service_name)
                    allowed_services.add(okx_wallet_service.legacy_service_name_for(wallet_address))
                    targets.append(
                        {
                            "kind": "dex",
                            "integration_id": integration.id,
                            "service": service_name,
                            "source_id": wallet_address,
                        }
                    )

        return targets, allowed_services

    async def _load_cex_config_override(self, integration_id: int) -> dict[str, Any] | None:
        from app.services.integration_service import IntegrationService

        secrets = await IntegrationService(self.session).get_integration_secrets(
            self.organization_id,
            integration_id,
        )
        api_key = str(secrets.get("api_key") or "").strip()
        api_secret = str(secrets.get("api_secret") or "").strip()
        if not api_key or not api_secret:
            return None
        config = {
            "apiKey": api_key,
            "secret": api_secret,
        }
        if secrets.get("api_password"):
            config["password"] = str(secrets["api_password"])
        if secrets.get("api_uid"):
            config["uid"] = str(secrets["api_uid"])
        return config

    def _get_cached_balance(self, service: str, integration_id: int | None = None) -> Optional[ServiceBalanceSchema]:
        state = self._get_cached_balance_state(service, integration_id=integration_id)
        return state["balance"] if state else None

    def _get_cached_balance_state(
        self,
        service: str,
        integration_id: int | None = None,
    ) -> Optional[dict[str, object]]:
        cache_key = (self.organization_id, service, integration_id)

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
        cache_key = (self.organization_id, service, balance.integration_id)
        with _balance_cache_lock:
            _balance_cache[cache_key] = {
                "balance": balance,
                "cached_at": time.time(),
                "state": "fresh",
            }

    async def _get_fallback_balance(
        self, service: str, integration_id: int | None = None
    ) -> Optional[ServiceBalanceSchema]:
        db_balance = await self.balance_repo.get_latest_balance(
            service,
            self.organization_id,
            integration_id=integration_id,
        )
        if db_balance:
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
                integration_id=getattr(db_balance, "integration_id", None),
                service=service,
                accounts=accounts,
                assets=assets,
                total_usd=db_balance.total_usd,
                updated_at=db_balance.updated_at,
                actual=False,
            )
        return None

    def _balance_from_record(self, record: Any, *, service: str) -> ServiceBalanceSchema:
        assets = [AssetSchema(**asset) for asset in (getattr(record, "assets", None) or [])]
        accounts = [
            AccountBalanceSchema(
                account_type=str(account.get("account_type") or "spot"),
                assets=[AssetSchema(**asset) for asset in (account.get("assets") or [])],
                total_usd=float(account.get("total_usd") or 0.0),
            )
            for account in (getattr(record, "accounts", None) or [])
        ]
        return ServiceBalanceSchema(
            integration_id=getattr(record, "integration_id", None),
            service=service,
            accounts=accounts,
            assets=assets,
            total_usd=float(getattr(record, "total_usd", 0.0) or 0.0),
            updated_at=getattr(record, "updated_at"),
            actual=bool(getattr(record, "actual", False)),
        )

    def _is_degraded_balance(self, balance: ServiceBalanceSchema) -> bool:
        return not balance.actual

    async def fetch_and_save_balance(
        self,
        service: str,
        balance: ServiceBalanceSchema,
        integration_id: int | None = None,
    ) -> ServiceBalanceSchema:
        persisted = await self.balance_repo.save_balance(
            service=service,
            assets=balance.assets,
            total_usd=balance.total_usd,
            actual=balance.actual,
            accounts=balance.accounts,
            organization_id=self.organization_id,
            integration_id=integration_id,
        )
        await self.status_repo.update_status(
            service,
            is_healthy=bool(balance.actual),
            last_error=None if balance.actual else "Degraded balance payload",
            organization_id=self.organization_id,
        )
        persisted_balance = self._balance_from_record(persisted, service=service)
        self._set_cached_balance(service, persisted_balance)
        return persisted_balance

    async def handle_fetch_error(
        self,
        service: str,
        error: Exception,
        integration_id: int | None = None,
    ) -> Optional[ServiceBalanceSchema]:
        await self.status_repo.update_status(
            service,
            is_healthy=False,
            last_error=str(error),
            organization_id=self.organization_id,
        )

        fallback = await self._get_fallback_balance(service, integration_id=integration_id)
        if fallback:
            await self.balance_repo.mark_as_stale(
                service,
                self.organization_id,
                integration_id=integration_id,
            )
            return fallback

        return None

    async def get_all_balances(self, force_refresh: bool = False) -> PortfolioResponse:
        targets, allowed_services = await self.load_balance_targets()

        if not allowed_services:
            return PortfolioResponse(
                total_usd=0.0,
                services=[],
                timestamp=datetime.now(timezone.utc),
            )

        all_balances_by_service: dict[str, ServiceBalanceSchema] = {}

        stale_revalidate_services: set[str] = set()

        if not force_refresh:
            fetch_targets: list[dict[str, Any]] = []
            revalidate_targets: list[dict[str, Any]] = []
            for target in targets:
                service = str(target["service"])
                integration_id = int(target["integration_id"])
                cache_key = f"{service}:{integration_id}"
                cached_state = self._get_cached_balance_state(service, integration_id=integration_id)
                if not cached_state:
                    metrics_service.inc("ccxt_cache_miss_total")
                    fetch_targets.append(target)
                    continue

                cached_balance = cached_state.get("balance")
                cache_state = str(cached_state.get("state"))

                if cache_state == "stale":
                    if settings.enable_ccxt_stale_revalidate:
                        if cached_balance:
                            all_balances_by_service[cache_key] = cached_balance
                        stale_revalidate_services.add(cache_key)
                        revalidate_targets.append(target)
                    else:
                        metrics_service.inc("ccxt_cache_miss_total")
                        fetch_targets.append(target)
                    continue

                if cached_balance:
                    all_balances_by_service[cache_key] = cached_balance

            targets = fetch_targets + revalidate_targets

        tasks = []
        task_map = {}
        parallelism = max(1, settings.exchange_parallelism)
        semaphore = asyncio.Semaphore(parallelism)

        async def _run_with_limit(coro):
            async with semaphore:
                return await coro

        for target in targets:
            if target["kind"] == "cex":
                config_override = await self._load_cex_config_override(int(target["integration_id"]))
                task = asyncio.create_task(
                    _run_with_limit(
                        ccxt_manager.fetch_balance(
                            str(target["source_id"]),
                            config_override=config_override,
                        )
                    )
                )
            else:
                task = asyncio.create_task(
                    _run_with_limit(okx_wallet_service.fetch_wallet_balance(str(target["source_id"])))
                )
            tasks.append(task)
            task_map[id(task)] = target

        if tasks:
            logger.info(f"Gathering balances from {len(tasks)} sources...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Gathering balances completed")

            for task, result in zip(tasks, results):
                target = task_map[id(task)]
                service = str(target["service"])
                integration_id = int(target["integration_id"])
                cache_key = f"{service}:{integration_id}"

                if isinstance(result, BaseException):
                    logger.warning(f"Failed to fetch {service}: {result}")
                    fallback = await self.handle_fetch_error(
                        service,
                        result,
                        integration_id=integration_id,
                    )
                    if fallback and cache_key not in stale_revalidate_services:
                        all_balances_by_service[cache_key] = fallback
                elif result is not None and self._is_degraded_balance(result):
                    logger.warning(f"Received degraded balance payload for {service}")
                    fallback = await self.handle_fetch_error(
                        service,
                        RuntimeError("Degraded balance payload"),
                        integration_id=integration_id,
                    )
                    if fallback and cache_key not in stale_revalidate_services:
                        all_balances_by_service[cache_key] = fallback
                elif result is not None:
                    saved = await self.fetch_and_save_balance(
                        service,
                        result,
                        integration_id=integration_id,
                    )
                    all_balances_by_service[cache_key] = saved

        all_balances = list(all_balances_by_service.values())
        all_balances = [balance for balance in all_balances if balance.service in allowed_services]
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

        targets, _ = await self.load_balance_targets()

        for target in targets:
            service = str(target["service"])
            integration_id = int(target["integration_id"])
            try:
                if target["kind"] == "cex":
                    config_override = await self._load_cex_config_override(integration_id)
                    result = await ccxt_manager.fetch_balance(
                        str(target["source_id"]),
                        config_override=config_override,
                    )
                else:
                    result = await okx_wallet_service.fetch_wallet_balance(str(target["source_id"]))
            except Exception as exc:
                await self.handle_fetch_error(service, exc, integration_id=integration_id)
                failed.append(service)
                continue

            if result is not None and self._is_degraded_balance(result):
                await self.handle_fetch_error(
                    service,
                    RuntimeError("Degraded balance payload"),
                    integration_id=integration_id,
                )
                failed.append(service)
                continue

            await self.fetch_and_save_balance(service, result, integration_id=integration_id)
            updated.append(service)

        if failed:
            job_status = "partial" if updated else "failed"
        else:
            job_status = "success"

        job = SyncJob(
            organization_id=self.organization_id,
            job_type="refresh",
            status=job_status,
            payload={"target_count": len(targets)},
            result={"updated": updated, "failed": failed},
            finished_at=datetime.now(timezone.utc),
        )
        self.session.add(job)
        await self.session.commit()

        return updated, failed
