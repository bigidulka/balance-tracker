from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import NotificationEvent, NotificationSetting, ServiceStatus, SyncJob, Transaction
from app.repositories.notifications import NotificationRepository
from app.schemas.notifications import (
    NotificationEventListResponse,
    NotificationEventResponse,
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
)

TRANSACTION_CURSOR_TYPE = "transactions"
TRANSACTION_CURSOR_SOURCE = "all"
SERVICE_STATUS_CURSOR_TYPE = "service_status"
SYNC_JOB_CURSOR_TYPE = "sync_jobs"
SUPPORTED_CEX_TRANSACTION_SOURCES = {
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
SUPPORTED_DEX_TRANSACTION_SOURCE_PREFIXES = ("evm_", "sol_", "sui_", "tron_", "ton_")


def _is_supported_transaction_source(source: str) -> bool:
    return source in SUPPORTED_CEX_TRANSACTION_SOURCES or source.startswith(
        SUPPORTED_DEX_TRANSACTION_SOURCE_PREFIXES
    )


def _is_dex_transaction_source(source: str) -> bool:
    return source.startswith(SUPPORTED_DEX_TRANSACTION_SOURCE_PREFIXES)


class NotificationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = NotificationRepository(session)

    @staticmethod
    def _settings_response(settings: NotificationSetting) -> NotificationSettingsResponse:
        return NotificationSettingsResponse(
            id=settings.id,
            organization_id=settings.organization_id,
            user_id=settings.user_id,
            enabled=bool(settings.enabled),
            system_enabled=bool(settings.system_enabled),
            transaction_enabled=bool(settings.transaction_enabled),
            balance_enabled=bool(settings.balance_enabled),
            min_balance_delta_usd=float(settings.min_balance_delta_usd or 0.0),
            min_balance_delta_percent=float(settings.min_balance_delta_percent or 0.0),
            muted_services=list(settings.muted_services or []),
            channels=list(settings.channels or []),
            created_at=settings.created_at,
            updated_at=settings.updated_at,
        )

    @staticmethod
    def _event_response(event: NotificationEvent) -> NotificationEventResponse:
        return NotificationEventResponse(
            id=event.id,
            organization_id=event.organization_id,
            user_id=event.user_id,
            integration_id=event.integration_id,
            event_type=event.event_type,
            severity=event.severity,
            source=event.source,
            title=event.title,
            body=event.body,
            payload=dict(event.payload or {}),
            dedupe_key=event.dedupe_key,
            status=event.status,
            error_message=event.error_message,
            send_after_at=event.send_after_at,
            sent_at=event.sent_at,
            created_at=event.created_at,
            updated_at=event.updated_at,
        )

    async def get_settings(
        self,
        *,
        organization_id: int,
        user_id: int,
    ) -> NotificationSettingsResponse:
        settings = await self.repo.get_or_create_settings(
            organization_id=organization_id,
            user_id=user_id,
        )
        return self._settings_response(settings)

    async def update_settings(
        self,
        *,
        organization_id: int,
        user_id: int,
        payload: NotificationSettingsUpdate,
    ) -> NotificationSettingsResponse:
        settings = await self.repo.get_or_create_settings(
            organization_id=organization_id,
            user_id=user_id,
        )
        was_transaction_enabled = bool(settings.transaction_enabled)
        values = payload.model_dump(exclude_unset=True)
        updated = await self.repo.update_settings(settings, **values)

        if not was_transaction_enabled and bool(updated.transaction_enabled):
            await self.seed_transaction_baseline(
                organization_id=organization_id,
                user_id=user_id,
            )

        return self._settings_response(updated)

    async def mark_event_sent(
        self,
        *,
        organization_id: int,
        user_id: int,
        event_id: int,
    ) -> bool:
        events = await self.repo.list_events(
            organization_id=organization_id,
            user_id=user_id,
            limit=1,
            offset=0,
        )
        event = next((item for item in events if int(item.id) == int(event_id)), None)
        if event is None:
            result = await self.session.execute(
                select(NotificationEvent).where(
                    NotificationEvent.organization_id == organization_id,
                    NotificationEvent.user_id == user_id,
                    NotificationEvent.id == event_id,
                )
            )
            event = result.scalar_one_or_none()
        if event is None:
            return False
        await self.repo.mark_sent(event_id)
        return True

    async def mark_event_failed(
        self,
        *,
        organization_id: int,
        user_id: int,
        event_id: int,
        error_message: str,
    ) -> bool:
        result = await self.session.execute(
            select(NotificationEvent).where(
                NotificationEvent.organization_id == organization_id,
                NotificationEvent.user_id == user_id,
                NotificationEvent.id == event_id,
            )
        )
        event = result.scalar_one_or_none()
        if event is None:
            return False
        await self.repo.mark_failed(event_id, error_message)
        return True

    async def list_events(
        self,
        *,
        organization_id: int,
        user_id: int,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> NotificationEventListResponse:
        events = await self.repo.list_events(
            organization_id=organization_id,
            user_id=user_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        total = await self.repo.count_events(
            organization_id=organization_id,
            user_id=user_id,
            status=status,
        )
        return NotificationEventListResponse(
            events=[self._event_response(event) for event in events],
            total_count=total,
        )

    async def enqueue_event(
        self,
        *,
        organization_id: int,
        user_id: int,
        event_type: str,
        title: str,
        body: str,
        dedupe_key: str,
        severity: str = "info",
        source: str = "system",
        integration_id: int | None = None,
        payload: dict[str, Any] | None = None,
        send_after_at: datetime | None = None,
    ) -> tuple[NotificationEventResponse, bool]:
        event, created = await self.repo.enqueue_event(
            organization_id=organization_id,
            user_id=user_id,
            event_type=event_type,
            title=title,
            body=body,
            dedupe_key=dedupe_key,
            severity=severity,
            source=source,
            integration_id=integration_id,
            payload=payload or {},
            send_after_at=send_after_at,
        )
        return self._event_response(event), created

    async def seed_transaction_baseline(
        self,
        *,
        organization_id: int,
        user_id: int,
    ) -> int:
        result = await self.session.execute(
            select(func.max(Transaction.id)).where(
                Transaction.organization_id == organization_id
            )
        )
        max_id = int(result.scalar_one() or 0)
        await self.repo.upsert_cursor(
            organization_id=organization_id,
            user_id=user_id,
            cursor_type=TRANSACTION_CURSOR_TYPE,
            source_key=TRANSACTION_CURSOR_SOURCE,
            cursor_value=str(max_id),
            payload={"seeded_at": datetime.now(timezone.utc).isoformat()},
        )
        return max_id

    async def ensure_default_settings_for_org(self, organization_id: int) -> int:
        users = await self.repo.list_active_telegram_users_for_org(organization_id)
        created_or_existing = 0
        for user in users:
            await self.repo.get_or_create_settings(
                organization_id=organization_id,
                user_id=user.id,
            )
            created_or_existing += 1
        return created_or_existing

    async def generate_system_status_events(
        self,
        *,
        organization_id: int,
    ) -> int:
        await self.ensure_default_settings_for_org(organization_id)
        settings_rows = await self.repo.list_enabled_settings(
            organization_id=organization_id,
            event_type="system",
        )
        if not settings_rows:
            return 0

        statuses = await self.repo.list_service_statuses(organization_id=organization_id)
        created_count = 0
        for settings in settings_rows:
            muted = {str(item).strip().lower() for item in (settings.muted_services or [])}
            for status in statuses:
                service = str(status.service or "").strip().lower()
                if not service or service in muted:
                    continue
                state = "healthy" if bool(status.is_healthy) else "unhealthy"
                last_check = status.last_check.isoformat() if status.last_check else ""
                cursor = await self.repo.get_cursor(
                    organization_id=settings.organization_id,
                    user_id=settings.user_id,
                    cursor_type=SERVICE_STATUS_CURSOR_TYPE,
                    source_key=service,
                )
                previous_state = None
                if cursor is not None and isinstance(cursor.payload, dict):
                    previous_state = cursor.payload.get("state")

                await self.repo.upsert_cursor(
                    organization_id=settings.organization_id,
                    user_id=settings.user_id,
                    cursor_type=SERVICE_STATUS_CURSOR_TYPE,
                    source_key=service,
                    cursor_value=last_check,
                    payload={
                        "state": state,
                        "last_error": status.last_error,
                        "last_check": last_check,
                    },
                )

                if previous_state is None or previous_state == state:
                    continue

                event_type = "service_recovered" if state == "healthy" else "service_unhealthy"
                severity = "info" if state == "healthy" else "warning"
                title = (
                    f"{service} recovered"
                    if state == "healthy"
                    else f"{service} needs attention"
                )
                body = self._service_status_body(status, state)
                _, created = await self.repo.enqueue_event(
                    organization_id=settings.organization_id,
                    user_id=settings.user_id,
                    event_type=event_type,
                    severity=severity,
                    source=service,
                    title=title,
                    body=body,
                    payload={
                        "service": service,
                        "state": state,
                        "previous_state": previous_state,
                        "last_error": status.last_error,
                        "last_check": last_check,
                    },
                    dedupe_key=f"service_status:{service}:{state}:{last_check}",
                )
                if created:
                    created_count += 1
        return created_count

    async def generate_sync_job_events(
        self,
        *,
        organization_id: int,
        limit_per_user: int = 100,
    ) -> int:
        await self.ensure_default_settings_for_org(organization_id)
        settings_rows = await self.repo.list_enabled_settings(
            organization_id=organization_id,
            event_type="system",
        )
        created_count = 0
        for settings in settings_rows:
            cursor = await self.repo.get_cursor(
                organization_id=settings.organization_id,
                user_id=settings.user_id,
                cursor_type=SYNC_JOB_CURSOR_TYPE,
                source_key="failed",
            )
            if cursor is None:
                result = await self.session.execute(
                    select(func.max(SyncJob.id)).where(
                        SyncJob.organization_id == settings.organization_id
                    )
                )
                max_id = int(result.scalar_one() or 0)
                await self.repo.upsert_cursor(
                    organization_id=settings.organization_id,
                    user_id=settings.user_id,
                    cursor_type=SYNC_JOB_CURSOR_TYPE,
                    source_key="failed",
                    cursor_value=str(max_id),
                    payload={"seeded_at": datetime.now(timezone.utc).isoformat()},
                )
                continue

            try:
                last_id = int(cursor.cursor_value or 0)
            except (TypeError, ValueError):
                last_id = 0

            result = await self.session.execute(
                select(SyncJob)
                .where(
                    SyncJob.organization_id == settings.organization_id,
                    SyncJob.id > last_id,
                    SyncJob.status == "failed",
                )
                .order_by(SyncJob.id)
                .limit(max(1, int(limit_per_user)))
            )
            jobs = list(result.scalars().all())
            max_seen = last_id
            for job in jobs:
                max_seen = max(max_seen, int(job.id))
                error = str(job.error_message or "")
                result_payload = job.result if isinstance(job.result, dict) else {}
                code = str(result_payload.get("code") or "")
                if code not in {"stale_running_job_recovered", "refresh_provider_timeout"}:
                    if "timeout" not in error.lower() and "stale" not in error.lower():
                        continue
                _, created = await self.repo.enqueue_event(
                    organization_id=settings.organization_id,
                    user_id=settings.user_id,
                    event_type="sync_job_failed",
                    severity="warning",
                    source="sync_jobs",
                    integration_id=job.integration_id,
                    title="Refresh job needs attention",
                    body=f"Refresh job {job.id} failed: {error or code or 'unknown error'}",
                    payload={
                        "job_id": job.id,
                        "integration_id": job.integration_id,
                        "code": code,
                        "error_message": error,
                    },
                    dedupe_key=f"sync_job:{job.id}:failed",
                )
                if created:
                    created_count += 1
            if max_seen != last_id:
                await self.repo.upsert_cursor(
                    organization_id=settings.organization_id,
                    user_id=settings.user_id,
                    cursor_type=SYNC_JOB_CURSOR_TYPE,
                    source_key="failed",
                    cursor_value=str(max_seen),
                    payload={"updated_at": datetime.now(timezone.utc).isoformat()},
                )
        return created_count

    async def generate_transaction_events(
        self,
        *,
        organization_id: int | None = None,
        limit_per_user: int = 100,
    ) -> int:
        settings_rows = await self.repo.list_enabled_settings(
            organization_id=organization_id,
            event_type="transaction",
        )
        created_count = 0
        for settings in settings_rows:
            cursor = await self.repo.get_cursor(
                organization_id=settings.organization_id,
                user_id=settings.user_id,
                cursor_type=TRANSACTION_CURSOR_TYPE,
                source_key=TRANSACTION_CURSOR_SOURCE,
            )
            if cursor is None:
                await self.seed_transaction_baseline(
                    organization_id=settings.organization_id,
                    user_id=settings.user_id,
                )
                continue

            try:
                last_id = int(cursor.cursor_value or 0)
            except (TypeError, ValueError):
                last_id = 0

            result = await self.session.execute(
                select(Transaction)
                .where(
                    Transaction.organization_id == settings.organization_id,
                    Transaction.id > last_id,
                    Transaction.status == "ok",
                )
                .order_by(Transaction.id)
                .limit(max(1, int(limit_per_user)))
            )
            transactions = list(result.scalars().all())
            max_seen = last_id
            muted = {str(item).strip().lower() for item in (settings.muted_services or [])}
            statuses = await self.repo.list_service_statuses(
                organization_id=settings.organization_id
            )
            healthy_services = {
                str(item.service or "").strip().lower()
                for item in statuses
                if bool(item.is_healthy)
            }
            for tx in transactions:
                max_seen = max(max_seen, int(tx.id))
                service_key = str(tx.service or "").strip().lower()
                if service_key in muted:
                    continue
                if not _is_supported_transaction_source(service_key):
                    continue
                if service_key not in healthy_services:
                    continue
                event, created = await self.repo.enqueue_event(
                    organization_id=settings.organization_id,
                    user_id=settings.user_id,
                    event_type="transaction",
                    severity="info",
                    source=tx.service,
                    integration_id=tx.integration_id,
                    title=self._transaction_title(tx),
                    body=self._transaction_body(tx),
                    payload={
                        "transaction_id": tx.id,
                        "tx_id": tx.tx_id,
                        "tx_type": tx.tx_type,
                        "service": tx.service,
                        "currency": tx.currency,
                        "amount": tx.amount,
                        "status": tx.status,
                        "tx_timestamp": tx.tx_timestamp.isoformat() if tx.tx_timestamp else None,
                    },
                    dedupe_key=f"transaction:{tx.id}:ok",
                )
                if created:
                    created_count += 1

            if max_seen != last_id:
                await self.repo.upsert_cursor(
                    organization_id=settings.organization_id,
                    user_id=settings.user_id,
                    cursor_type=TRANSACTION_CURSOR_TYPE,
                    source_key=TRANSACTION_CURSOR_SOURCE,
                    cursor_value=str(max_seen),
                    payload={"updated_at": datetime.now(timezone.utc).isoformat()},
                )
        return created_count

    @staticmethod
    def _service_status_body(status: ServiceStatus, state: str) -> str:
        service = str(status.service or "service")
        if state == "healthy":
            return f"{service} is healthy again."
        error = str(status.last_error or "Integration refresh requires attention.")
        return f"{service} requires attention: {error}"

    @staticmethod
    def _transaction_title(tx: Transaction) -> str:
        service = str(tx.service or "")
        direction = "Deposit" if tx.tx_type == "deposit" else "Withdrawal"
        if _is_dex_transaction_source(service):
            direction = "Incoming" if tx.tx_type == "deposit" else "Outgoing"
            return f"DEX {direction} transaction"
        return f"{direction} on {tx.service}"

    @staticmethod
    def _transaction_body(tx: Transaction) -> str:
        service = str(tx.service or "")
        direction = "Received" if tx.tx_type == "deposit" else "Sent"
        if _is_dex_transaction_source(service):
            direction = "Received" if tx.tx_type == "deposit" else "Sent"
        amount = float(tx.amount or 0.0)
        timestamp = tx.tx_timestamp.isoformat() if tx.tx_timestamp else "unknown time"
        network = f" on {tx.network}" if tx.network else ""
        if _is_dex_transaction_source(service):
            return f"{direction} {amount:g} {tx.currency}{network} ({timestamp})"
        return f"{direction} {amount:g} {tx.currency} via {tx.service} ({timestamp})"
