from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, desc, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import (
    NotificationCursor,
    NotificationEvent,
    NotificationSetting,
    OrganizationMembership,
    ServiceStatus,
    User,
)


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_settings(
        self,
        *,
        organization_id: int,
        user_id: int,
    ) -> NotificationSetting | None:
        result = await self.session.execute(
            select(NotificationSetting).where(
                NotificationSetting.organization_id == organization_id,
                NotificationSetting.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create_settings(
        self,
        *,
        organization_id: int,
        user_id: int,
    ) -> NotificationSetting:
        existing = await self.get_settings(
            organization_id=organization_id,
            user_id=user_id,
        )
        if existing is not None:
            return existing

        settings = NotificationSetting(
            organization_id=organization_id,
            user_id=user_id,
            enabled=True,
            system_enabled=True,
            transaction_enabled=False,
            balance_enabled=False,
            min_balance_delta_usd=10.0,
            min_balance_delta_percent=5.0,
            muted_services=[],
            channels=["telegram"],
        )
        self.session.add(settings)
        try:
            await self.session.commit()
            await self.session.refresh(settings)
            return settings
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_settings(
                organization_id=organization_id,
                user_id=user_id,
            )
            if existing is None:
                raise
            return existing

    async def update_settings(
        self,
        settings: NotificationSetting,
        **values: Any,
    ) -> NotificationSetting:
        allowed = {
            "enabled",
            "system_enabled",
            "transaction_enabled",
            "balance_enabled",
            "min_balance_delta_usd",
            "min_balance_delta_percent",
            "muted_services",
            "channels",
        }
        for key, value in values.items():
            if key in allowed and value is not None:
                setattr(settings, key, value)
        settings.updated_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(settings)
        return settings

    async def list_enabled_settings(
        self,
        *,
        organization_id: int | None = None,
        event_type: str | None = None,
    ) -> list[NotificationSetting]:
        predicates = [NotificationSetting.enabled == True]
        if organization_id is not None:
            predicates.append(NotificationSetting.organization_id == organization_id)
        if event_type == "transaction":
            predicates.append(NotificationSetting.transaction_enabled == True)
        elif event_type == "balance":
            predicates.append(NotificationSetting.balance_enabled == True)
        elif event_type == "system":
            predicates.append(NotificationSetting.system_enabled == True)

        result = await self.session.execute(
            select(NotificationSetting).where(*predicates).order_by(NotificationSetting.id)
        )
        return list(result.scalars().all())

    async def list_active_telegram_users_for_org(self, organization_id: int) -> list[User]:
        result = await self.session.execute(
            select(User)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(
                OrganizationMembership.organization_id == organization_id,
                User.is_active == True,
                User.telegram_user_id.is_not(None),
            )
            .order_by(User.id)
        )
        return list(result.scalars().all())

    async def get_cursor(
        self,
        *,
        organization_id: int,
        user_id: int,
        cursor_type: str,
        source_key: str,
    ) -> NotificationCursor | None:
        result = await self.session.execute(
            select(NotificationCursor).where(
                NotificationCursor.organization_id == organization_id,
                NotificationCursor.user_id == user_id,
                NotificationCursor.cursor_type == cursor_type,
                NotificationCursor.source_key == source_key,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_cursor(
        self,
        *,
        organization_id: int,
        user_id: int,
        cursor_type: str,
        source_key: str,
        cursor_value: str | None,
        payload: dict[str, Any] | None = None,
    ) -> NotificationCursor:
        cursor = await self.get_cursor(
            organization_id=organization_id,
            user_id=user_id,
            cursor_type=cursor_type,
            source_key=source_key,
        )
        if cursor is None:
            cursor = NotificationCursor(
                organization_id=organization_id,
                user_id=user_id,
                cursor_type=cursor_type,
                source_key=source_key,
                cursor_value=cursor_value,
                payload=payload or {},
            )
            self.session.add(cursor)
        else:
            cursor.cursor_value = cursor_value
            cursor.payload = payload or {}
            cursor.updated_at = datetime.now(timezone.utc)

        try:
            await self.session.commit()
            await self.session.refresh(cursor)
            return cursor
        except IntegrityError:
            await self.session.rollback()
            cursor = await self.get_cursor(
                organization_id=organization_id,
                user_id=user_id,
                cursor_type=cursor_type,
                source_key=source_key,
            )
            if cursor is None:
                raise
            cursor.cursor_value = cursor_value
            cursor.payload = payload or {}
            cursor.updated_at = datetime.now(timezone.utc)
            await self.session.commit()
            await self.session.refresh(cursor)
            return cursor

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
    ) -> tuple[NotificationEvent, bool]:
        existing = await self.get_event_by_dedupe_key(
            organization_id=organization_id,
            user_id=user_id,
            dedupe_key=dedupe_key,
        )
        if existing is not None:
            return existing, False

        event = NotificationEvent(
            organization_id=organization_id,
            user_id=user_id,
            integration_id=integration_id,
            event_type=event_type,
            severity=severity,
            source=source,
            title=title,
            body=body,
            payload=payload or {},
            dedupe_key=dedupe_key,
            status="pending",
            send_after_at=send_after_at,
        )
        self.session.add(event)
        try:
            await self.session.commit()
            await self.session.refresh(event)
            return event, True
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_event_by_dedupe_key(
                organization_id=organization_id,
                user_id=user_id,
                dedupe_key=dedupe_key,
            )
            if existing is None:
                raise
            return existing, False

    async def get_event_by_dedupe_key(
        self,
        *,
        organization_id: int,
        user_id: int,
        dedupe_key: str,
    ) -> NotificationEvent | None:
        result = await self.session.execute(
            select(NotificationEvent).where(
                NotificationEvent.organization_id == organization_id,
                NotificationEvent.user_id == user_id,
                NotificationEvent.dedupe_key == dedupe_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_events(
        self,
        *,
        organization_id: int,
        user_id: int,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NotificationEvent]:
        predicates = [
            NotificationEvent.organization_id == organization_id,
            NotificationEvent.user_id == user_id,
        ]
        if status:
            predicates.append(NotificationEvent.status == status)
        result = await self.session.execute(
            select(NotificationEvent)
            .where(and_(*predicates))
            .order_by(desc(NotificationEvent.created_at), desc(NotificationEvent.id))
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_events(
        self,
        *,
        organization_id: int,
        user_id: int,
        status: str | None = None,
    ) -> int:
        predicates = [
            NotificationEvent.organization_id == organization_id,
            NotificationEvent.user_id == user_id,
        ]
        if status:
            predicates.append(NotificationEvent.status == status)
        result = await self.session.execute(
            select(func.count(NotificationEvent.id)).where(and_(*predicates))
        )
        return int(result.scalar_one() or 0)

    async def claim_pending_events(self, *, limit: int = 100) -> list[NotificationEvent]:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(NotificationEvent)
            .where(
                NotificationEvent.status == "pending",
                (NotificationEvent.send_after_at.is_(None))
                | (NotificationEvent.send_after_at <= now),
            )
            .order_by(NotificationEvent.created_at, NotificationEvent.id)
            .limit(limit)
        )
        events = list(result.scalars().all())
        if not events:
            return []
        event_ids = [event.id for event in events]
        await self.session.execute(
            update(NotificationEvent)
            .where(NotificationEvent.id.in_(event_ids), NotificationEvent.status == "pending")
            .values(status="sending", updated_at=now)
        )
        await self.session.commit()
        result = await self.session.execute(
            select(NotificationEvent).where(NotificationEvent.id.in_(event_ids))
        )
        return list(result.scalars().all())

    async def mark_sent(self, event_id: int) -> None:
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(NotificationEvent)
            .where(NotificationEvent.id == event_id)
            .values(status="sent", sent_at=now, error_message=None, updated_at=now)
        )
        await self.session.commit()

    async def mark_failed(self, event_id: int, error_message: str) -> None:
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(NotificationEvent)
            .where(NotificationEvent.id == event_id)
            .values(status="failed", error_message=error_message[:1000], updated_at=now)
        )
        await self.session.commit()

    async def list_service_statuses(
        self,
        *,
        organization_id: int,
    ) -> list[ServiceStatus]:
        result = await self.session.execute(
            select(ServiceStatus)
            .where(ServiceStatus.organization_id == organization_id)
            .order_by(ServiceStatus.service)
        )
        return list(result.scalars().all())
