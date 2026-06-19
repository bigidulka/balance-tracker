import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

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
from app.services.debank_sdk_client import debank_sdk_client
from app.services.integration_keys import service_key_for_integration
from app.services.integration_service import IntegrationService

logger = logging.getLogger(__name__)
settings = get_settings()

_transactions_refresh_inflight: dict[str, asyncio.Task] = {}
_transactions_refresh_inflight_lock = asyncio.Lock()

SUPPORTED_CEX_TRANSACTION_EXCHANGES = {
    "binance",
    "bingx",
    "bitget",
    "bitmart",
    "bybit",
    "coinex",
    "gateio",
    "htx",
    "kucoin",
    "mexc",
    "okx",
    "poloniex",
    "xt",
}
SUPPORTED_DEX_TRANSACTION_SERVICE_PREFIXES = ("evm_", "sol_")


class TransactionService:
    """Сервис для работы с транзакциями (вводы/выводы)"""

    def __init__(self, session: AsyncSession, organization_id: int = 1):
        self.session = session
        self.organization_id = organization_id
        self.tx_repo = TransactionRepository(session)
        self.status_repo = ServiceStatusRepository(session)
        self.entitlements = EntitlementsService(session)
        self.integration_service = IntegrationService(session)

    async def _load_active_exchange_targets(self) -> list[dict[str, Any]]:
        integrations = await self.integration_service.list_integrations(
            self.organization_id,
            include_inactive=False,
        )
        targets: list[dict[str, Any]] = []
        for integration in integrations:
            if str(integration.kind or "").strip().lower() != "cex":
                continue
            exchange_code = str(integration.exchange_code or "").strip().lower()
            if not exchange_code:
                continue
            if exchange_code not in SUPPORTED_CEX_TRANSACTION_EXCHANGES:
                continue
            service_status = await self.status_repo.get_status(
                exchange_code,
                organization_id=self.organization_id,
            )
            if service_status is not None and not bool(service_status.is_healthy):
                continue
            targets.append(
                {
                    "kind": "cex",
                    "integration_id": integration.id,
                    "service": exchange_code,
                    "exchange_id": exchange_code,
                }
            )
        return targets

    async def _load_active_dex_targets(self) -> list[dict[str, Any]]:
        integrations = await self.integration_service.list_integrations(
            self.organization_id,
            include_inactive=False,
        )
        targets: list[dict[str, Any]] = []
        for integration in integrations:
            if str(integration.kind or "").strip().lower() != "dex":
                continue
            wallet_address = str(integration.wallet_address or "").strip()
            if not wallet_address:
                continue
            service_key = service_key_for_integration(integration)
            if not service_key.startswith(SUPPORTED_DEX_TRANSACTION_SERVICE_PREFIXES):
                continue
            service_status = await self.status_repo.get_status(
                service_key,
                organization_id=self.organization_id,
            )
            if service_status is not None and not bool(service_status.is_healthy):
                continue
            targets.append(
                {
                    "kind": "dex",
                    "integration_id": integration.id,
                    "service": service_key,
                    "wallet_address": wallet_address,
                }
            )
        return targets

    async def _load_cex_config_override(self, integration_id: int) -> dict[str, Any] | None:
        secrets = await self.integration_service.get_integration_secrets(
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

    async def get_transactions(
        self,
        service: Optional[str] = None,
        integration_id: Optional[int] = None,
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
            integration_id=integration_id,
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
            integration_id=integration_id,
            tx_type=tx_type,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        tx_schemas = [
            TransactionSchema(
                id=tx.id,
                integration_id=tx.integration_id,
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
            refreshed_at=datetime.now(timezone.utc),
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

        explicit_exchange_ids = [
            str(exchange_id).strip().lower()
            for exchange_id in (exchange_ids or [])
            if str(exchange_id).strip().lower() in SUPPORTED_CEX_TRANSACTION_EXCHANGES
        ]
        targets = []
        if not explicit_exchange_ids:
            targets = [
                *(await self._load_active_exchange_targets()),
                *(await self._load_active_dex_targets()),
            ]

        if not explicit_exchange_ids and not targets:
            return TransactionsRefreshResponse(
                status="ok",
                message="No active integrations to refresh",
                new_transactions=0,
                updated_transactions=0,
                services_checked=[],
                failed_services=[],
            )

        target_keys = sorted(
            explicit_exchange_ids
            or [
                f"{target['kind']}:{target['service']}:{target['integration_id']}"
                for target in targets
            ]
        )
        inflight_key = f"org:{self.organization_id}:since:{since_hours}:targets:{','.join(target_keys)}"

        async def _refresh_impl() -> TransactionsRefreshResponse:
            since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

            new_count = 0
            updated_count = 0
            checked_services = []
            failed_services = []

            if explicit_exchange_ids:
                results_by_exchange = await ccxt_manager.fetch_transactions_all_exchanges(
                    explicit_exchange_ids,
                    since=since,
                    limit=100,
                )
                iterable_targets = [
                    {
                        "exchange_id": exchange_id,
                        "integration_id": None,
                        "result": result,
                    }
                    for exchange_id, result in results_by_exchange.items()
                ]
            else:
                iterable_targets = targets

            for target in iterable_targets:
                service_key = str(target.get("service") or target.get("exchange_id") or "")
                integration_id = target.get("integration_id")
                try:
                    if "result" in target:
                        result = target["result"]
                    elif target.get("kind") == "dex":
                        result = await debank_sdk_client.fetch_wallet_transactions(
                            str(target["wallet_address"]),
                            service=service_key,
                            integration_id=int(integration_id) if integration_id is not None else None,
                            since=since,
                            limit=20,
                        )
                    else:
                        config_override = await self._load_cex_config_override(int(integration_id))
                        result = await ccxt_manager.fetch_all_transactions(
                            service_key,
                            since=since,
                            limit=100,
                            config_override=config_override,
                        )
                except Exception as fetch_exc:
                    result = fetch_exc

                if isinstance(result, Exception):
                    logger.warning(
                        f"Failed to fetch transactions from {service_key}: {result}"
                    )
                    failed_services.append(service_key)
                    continue

                checked_services.append(service_key)
                scoped_transactions = [
                    tx.model_copy(update={"integration_id": integration_id})
                    for tx in result
                ]

                try:
                    exchange_new, exchange_updated = await self.tx_repo.save_transactions_batch(
                        scoped_transactions,
                        organization_id=self.organization_id,
                    )
                    new_count += exchange_new
                    updated_count += exchange_updated
                except Exception as e:
                    logger.error(
                        f"Failed to save transactions batch for {service_key}: {e}"
                    )
                    for tx_data in scoped_transactions:
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
                integration_id=tx.integration_id,
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
