from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_organization_id, get_current_user, require_role
from app.models.balance import User
from app.schemas.notifications import (
    NotificationEventListResponse,
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
)
from pydantic import BaseModel
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class NotificationEventFailureRequest(BaseModel):
    error_message: str = ""


@router.get("/settings", response_model=NotificationSettingsResponse)
async def get_notification_settings(
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
    _: object = Depends(require_role("viewer")),
):
    return await NotificationService(db).get_settings(
        organization_id=organization_id,
        user_id=user.id,
    )


@router.patch("/settings", response_model=NotificationSettingsResponse)
async def update_notification_settings(
    payload: NotificationSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
    _: object = Depends(require_role("member")),
):
    return await NotificationService(db).update_settings(
        organization_id=organization_id,
        user_id=user.id,
        payload=payload,
    )


@router.get("/events", response_model=NotificationEventListResponse)
async def list_notification_events(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
    _: object = Depends(require_role("viewer")),
):
    return await NotificationService(db).list_events(
        organization_id=organization_id,
        user_id=user.id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post("/events/{event_id}/sent")
async def mark_notification_event_sent(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
    _: object = Depends(require_role("member")),
):
    ok = await NotificationService(db).mark_event_sent(
        organization_id=organization_id,
        user_id=user.id,
        event_id=event_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Notification event not found")
    return {"status": "ok"}


@router.post("/events/{event_id}/failed")
async def mark_notification_event_failed(
    event_id: int,
    payload: NotificationEventFailureRequest,
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    user: User = Depends(get_current_user),
    _: object = Depends(require_role("member")),
):
    ok = await NotificationService(db).mark_event_failed(
        organization_id=organization_id,
        user_id=user.id,
        event_id=event_id,
        error_message=payload.error_message,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Notification event not found")
    return {"status": "ok"}


@router.post("/generate")
async def generate_notification_events(
    kind: Literal["system", "transactions", "all"] = Query(default="all"),
    db: AsyncSession = Depends(get_db),
    organization_id: int = Depends(get_current_organization_id),
    _: object = Depends(require_role("member")),
):
    service = NotificationService(db)
    system_events = 0
    sync_job_events = 0
    transaction_events = 0
    if kind in {"system", "all"}:
        system_events = await service.generate_system_status_events(
            organization_id=organization_id,
        )
        sync_job_events = await service.generate_sync_job_events(
            organization_id=organization_id,
        )
    if kind in {"transactions", "all"}:
        transaction_events = await service.generate_transaction_events(
            organization_id=organization_id,
        )
    return {
        "status": "ok",
        "system_events": system_events,
        "sync_job_events": sync_job_events,
        "transaction_events": transaction_events,
    }
