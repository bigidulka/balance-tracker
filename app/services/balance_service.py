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
from app.services.okx_wallet import okx_wallet_service, OKXWalletService
from app.services.tron_ton_service import TronTonService, tron_ton_service
from app.services.sui_service import SuiService, sui_service
from app.services.metrics_service import metrics_service

logger = logging.getLogger(__name__)
settings = get_settings()

_balance_cache: dict[tuple[int, str, int | None], dict[str, object]] = {}
_balance_cache_lock = Lock()
_refresh_locks: dict[int, asyncio.Lock] = {}
_refresh_locks_guard = Lock()
_provider_circuit_state: dict[str, dict[str, float | int]] = {}
_provider_circuit_guard = Lock()
_PROVIDER_TIMEOUT_OVERRIDES_SECONDS: dict[str, float] = {
    "gateio": 12.0,
}
_PROVIDER_CIRCUIT_OPEN_AFTER_FAILURES = 2
_PROVIDER_CIRCUIT_OPEN_SECONDS = 180.0

# All wallet service-name prefixes (used for DEX detection in routers/filters).
WALLET_SERVICE_PREFIXES: tuple[str, ...] = (
    "evm_",
    "sol_",
    "tron_",
    "ton_",
    "sui_",
    "okx_wallet",
)


def wallet_service_name_for(wallet_address: str, provider: str) -> str:
    """Return canonical service key for a DEX wallet, based on provider + address.

    evm_wallet  (okx_wallet provider, EVM addr)  → evm_{addr}
    sol_wallet  (okx_wallet provider, SOL addr)  → sol_{addr}
    tron_wallet (tron_ton provider, TRON addr)   → tron_{addr}
    ton_wallet  (tron_ton provider, TON addr)    → ton_{addr}
    sui_wallet  (sui provider)                   → sui_{addr}
    """
    p = (provider or "").strip().lower()
    if p == "tron_ton":
        return TronTonService.service_name_for(wallet_address)
    if p == "sui":
        return SuiService.service_name_for(wallet_address)
    # okx_wallet and anything else → OKXWalletService (evm_ / sol_)
    return OKXWalletService.service_name_for(wallet_address)


class BalanceService:
    def __init__(self, session: AsyncSession, organization_id: int = 1):
        self.session = session
        self.organization_id = organization_id
        self.balance_repo = BalanceRepository(session)
        self.status_repo = ServiceStatusRepository(session)
        self.entitlements = EntitlementsService(session)

    def _refresh_lock(self) -> asyncio.Lock:
        with _refresh_locks_guard:
            lock = _refresh_locks.get(self.organization_id)
            if lock is None:
                lock = asyncio.Lock()
                _refresh_locks[self.organization_id] = lock
            return lock

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
                provider = str(integration.provider or "").strip().lower()
                if wallet_address:
                    service_name = wallet_service_name_for(wallet_address, provider)
                    allowed_services.add(service_name)
                    # Keep legacy prefix in allowed set for old DB rows during rollover
                    allowed_services.add(
                        okx_wallet_service.legacy_service_name_for(wallet_address)
                    )
                    targets.append(
                        {
                            "kind": "dex",
                            "integration_id": integration.id,
                            "service": service_name,
                            "source_id": wallet_address,
                            "provider": provider,
                        }
                    )

        return targets, allowed_services

    @staticmethod
    def _build_cex_config_override_from_secrets(
        secrets: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        secrets = secrets or {}
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

    async def _load_cex_config_override(
        self, integration_id: int
    ) -> dict[str, Any] | None:
        from app.services.integration_service import IntegrationService

        secrets = await IntegrationService(self.session).get_integration_secrets(
            self.organization_id,
            integration_id,
        )
        return self._build_cex_config_override_from_secrets(secrets)

    def _get_cached_balance(
        self, service: str, integration_id: int | None = None
    ) -> Optional[ServiceBalanceSchema]:
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

    def _balance_from_record(
        self, record: Any, *, service: str
    ) -> ServiceBalanceSchema:
        assets = [
            AssetSchema(**asset) for asset in (getattr(record, "assets", None) or [])
        ]
        accounts = [
            AccountBalanceSchema(
                account_type=str(account.get("account_type") or "spot"),
                assets=[
                    AssetSchema(**asset) for asset in (account.get("assets") or [])
                ],
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

    def _provider_timeout_seconds(self, service: str) -> float | None:
        raw = _PROVIDER_TIMEOUT_OVERRIDES_SECONDS.get(service)
        if raw is not None:
            return max(0.1, float(raw))
        return None

    def _provider_circuit_is_open(self, service: str) -> bool:
        with _provider_circuit_guard:
            state = _provider_circuit_state.get(service)
            if not state:
                return False
            opened_until = float(state.get("opened_until", 0.0) or 0.0)
            if opened_until <= time.time():
                _provider_circuit_state.pop(service, None)
                return False
            return True

    def _provider_circuit_record_success(self, service: str) -> None:
        with _provider_circuit_guard:
            _provider_circuit_state.pop(service, None)

    def _provider_circuit_record_failure(self, service: str) -> None:
        with _provider_circuit_guard:
            state = _provider_circuit_state.setdefault(
                service, {"failures": 0, "opened_until": 0.0}
            )
            failures = int(state.get("failures", 0) or 0) + 1
            state["failures"] = failures
            if failures >= _PROVIDER_CIRCUIT_OPEN_AFTER_FAILURES:
                state["opened_until"] = time.time() + _PROVIDER_CIRCUIT_OPEN_SECONDS

    async def _fetch_target_balance(
        self,
        target: dict[str, Any],
        *,
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema | None:
        service = str(target["service"])
        kind = str(target["kind"])
        started_at = time.perf_counter()

        if self._provider_circuit_is_open(service):
            logger.warning(
                "balance_fetch_target service=%s kind=%s duration_ms=0 ok=0 skipped=circuit_open",
                service,
                kind,
            )
            raise RuntimeError(f"Provider circuit open: {service}")

        try:
            if kind == "cex":
                runner = ccxt_manager.fetch_balance(
                    str(target["source_id"]),
                    config_override=config_override,
                )
            else:
                provider = str(target.get("provider") or "").strip().lower()
                source_id = str(target["source_id"])
                if provider == "tron_ton":
                    runner = tron_ton_service.fetch_wallet_balance(source_id)
                elif provider == "sui":
                    runner = sui_service.fetch_wallet_balance(source_id)
                else:
                    runner = okx_wallet_service.fetch_wallet_balance(source_id)

            timeout_seconds = self._provider_timeout_seconds(service)
            if timeout_seconds is not None:
                result = await asyncio.wait_for(runner, timeout=timeout_seconds)
            else:
                result = await runner

            self._provider_circuit_record_success(service)
            metrics_service.observe_duration(
                f"balance_fetch_target_{kind}_seconds", time.perf_counter() - started_at
            )
            logger.info(
                "balance_fetch_target service=%s kind=%s duration_ms=%d ok=1",
                service,
                kind,
                int((time.perf_counter() - started_at) * 1000),
            )
            return result
        except Exception:
            self._provider_circuit_record_failure(service)
            metrics_service.observe_duration(
                f"balance_fetch_target_{kind}_seconds", time.perf_counter() - started_at
            )
            logger.warning(
                "balance_fetch_target service=%s kind=%s duration_ms=%d ok=0",
                service,
                kind,
                int((time.perf_counter() - started_at) * 1000),
            )
            raise

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

        fallback = await self._get_fallback_balance(
            service, integration_id=integration_id
        )
        if fallback:
            await self.balance_repo.mark_as_stale(
                service,
                self.organization_id,
                integration_id=integration_id,
            )
            return fallback

        return None

    async def get_all_balances(self, force_refresh: bool = False) -> PortfolioResponse:
        request_started_at = time.perf_counter()
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
                cached_state = self._get_cached_balance_state(
                    service, integration_id=integration_id
                )
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

        cex_integration_ids = [
            int(target["integration_id"])
            for target in targets
            if target["kind"] == "cex"
        ]
        cex_secret_map: dict[int, dict[str, str]] = {}
        if cex_integration_ids:
            from app.services.integration_service import IntegrationService

            cex_secret_map = await IntegrationService(self.session).get_integration_secrets_bulk(
                self.organization_id,
                cex_integration_ids,
            )

        for target in targets:
            if target["kind"] == "cex":
                integration_id = int(target["integration_id"])
                config_override = self._build_cex_config_override_from_secrets(
                    cex_secret_map.get(integration_id)
                )
                task = asyncio.create_task(
                    _run_with_limit(
                        self._fetch_target_balance(
                            target,
                            config_override=config_override,
                        )
                    )
                )
            else:
                task = asyncio.create_task(
                    _run_with_limit(self._fetch_target_balance(target))
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
        all_balances = [
            balance for balance in all_balances if balance.service in allowed_services
        ]
        total_usd = sum(b.total_usd for b in all_balances)

        metrics_service.observe_duration(
            "balance_get_all_seconds", time.perf_counter() - request_started_at
        )
        logger.info(
            "balance_get_all organization_id=%s targets=%s services=%s total_ms=%d force_refresh=%s",
            self.organization_id,
            len(targets),
            len(all_balances),
            int((time.perf_counter() - request_started_at) * 1000),
            int(bool(force_refresh)),
        )
        return PortfolioResponse(
            total_usd=total_usd,
            services=all_balances,
            timestamp=datetime.now(timezone.utc),
        )

    async def refresh_all(self) -> tuple[list[str], list[str]]:
        request_started_at = time.perf_counter()
        updated: list[str] = []
        failed: list[str] = []

        refresh_lock = self._refresh_lock()
        wait_started = time.perf_counter()
        async with refresh_lock:
            lock_wait = time.perf_counter() - wait_started
            if lock_wait > 0.001:
                metrics_service.observe_duration("balance_refresh_lock_wait_seconds", lock_wait)
                logger.info(
                    "balance_refresh_lock_wait organization_id=%s wait_ms=%d",
                    self.organization_id,
                    int(lock_wait * 1000),
                )

            await self.entitlements.ensure_refresh_interval_for_organization(
                self.organization_id
            )

            targets, _ = await self.load_balance_targets()
            if targets:
                parallelism = max(1, settings.exchange_parallelism)
                semaphore = asyncio.Semaphore(parallelism)

                async def _run_with_limit(coro):
                    async with semaphore:
                        return await coro

                cex_integration_ids = [
                    int(target["integration_id"])
                    for target in targets
                    if target["kind"] == "cex"
                ]
                cex_secret_map: dict[int, dict[str, str]] = {}
                if cex_integration_ids:
                    from app.services.integration_service import IntegrationService

                    cex_secret_map = await IntegrationService(self.session).get_integration_secrets_bulk(
                        self.organization_id,
                        cex_integration_ids,
                    )

                tasks = []
                task_map: dict[int, dict[str, Any]] = {}
                for target in targets:
                    integration_id = int(target["integration_id"])
                    if target["kind"] == "cex":
                        config_override = self._build_cex_config_override_from_secrets(
                            cex_secret_map.get(integration_id)
                        )
                        task = asyncio.create_task(
                            _run_with_limit(
                                self._fetch_target_balance(
                                    target,
                                    config_override=config_override,
                                )
                            )
                        )
                    else:
                        task = asyncio.create_task(
                            _run_with_limit(self._fetch_target_balance(target))
                        )
                    tasks.append(task)
                    task_map[id(task)] = target

                results = await asyncio.gather(*tasks, return_exceptions=True)
                for task, result in zip(tasks, results):
                    target = task_map[id(task)]
                    service = str(target["service"])
                    integration_id = int(target["integration_id"])
                    if isinstance(result, BaseException):
                        await self.handle_fetch_error(
                            service, result, integration_id=integration_id
                        )
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

                    await self.fetch_and_save_balance(
                        service, result, integration_id=integration_id
                    )
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

        metrics_service.observe_duration(
            "balance_refresh_all_seconds", time.perf_counter() - request_started_at
        )
        logger.info(
            "balance_refresh_all organization_id=%s targets=%s updated=%s failed=%s total_ms=%d",
            self.organization_id,
            len(targets),
            len(updated),
            len(failed),
            int((time.perf_counter() - request_started_at) * 1000),
        )

        return updated, failed
