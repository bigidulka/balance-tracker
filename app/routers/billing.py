import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import IdentityContext, require_role
from app.models.balance import Plan, Subscription
from app.services.audit_log_service import AuditLogService
from app.services.billing_service import BillingService
from app.services.logging_context import get_request_logger, request_log_context
from app.services.metrics_service import metrics_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


async def _resolve_target_plan(
    db: AsyncSession,
    payload: dict[str, Any],
) -> Plan | None:
    plan_code = payload.get("plan_code") or payload.get("plan") or payload.get("tier")
    if not isinstance(plan_code, str) or not plan_code.strip():
        return None

    normalized = plan_code.strip().lower()
    if normalized not in {"free", "full"}:
        return None

    result = await db.execute(
        select(Plan)
        .where(and_(Plan.code == normalized, Plan.is_active == True))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _switch_subscription_if_needed(
    db: AsyncSession,
    organization_id: int,
    plan: Plan,
) -> bool:
    current_result = await db.execute(
        select(Subscription)
        .where(
            and_(
                Subscription.organization_id == organization_id,
                Subscription.status == "active",
            )
        )
        .order_by(Subscription.updated_at.desc())
        .limit(1)
    )
    current = current_result.scalar_one_or_none()

    if current and current.plan_id == plan.id:
        return False

    if current:
        current.status = "replaced"

    db.add(
        Subscription(
            organization_id=organization_id,
            plan_id=plan.id,
            status="active",
        )
    )
    await db.commit()
    return True


@router.post("/webhooks/{provider}")
async def billing_webhook(
    provider: str,
    payload: dict[str, Any],
    x_event_id: str | None = Header(default=None, alias="x-event-id"),
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("member")),
):
    external_event_id = x_event_id or str(payload.get("id") or payload.get("event_id") or "")
    if not external_event_id:
        raise HTTPException(status_code=400, detail="Missing external event id")

    service = BillingService(db)
    event, created = await service.process_webhook_event(
        provider=provider,
        external_event_id=external_event_id,
        payload=payload,
        organization_id=identity.organization.id,
    )

    switched_plan = False
    switched_to: str | None = None
    if created:
        target_plan = await _resolve_target_plan(db, payload)
        if target_plan is not None:
            switched_plan = await _switch_subscription_if_needed(
                db,
                organization_id=identity.organization.id,
                plan=target_plan,
            )
            switched_to = target_plan.code

    request_id = f"billing-{provider}-{external_event_id}"
    await AuditLogService(db).log_event(
        organization_id=identity.organization.id,
        user_id=identity.user.id,
        request_id=request_id,
        action="billing.webhook.received" if created else "billing.webhook.duplicate",
        resource_type="billing_webhook",
        resource_id=str(event.id),
        details={
            "provider": provider,
            "external_event_id": external_event_id,
            "switched_plan": switched_plan,
            "plan_code": switched_to,
        },
    )

    metrics_service.inc("billing_webhook_total")
    if not created:
        metrics_service.inc("billing_webhook_duplicate_total")
    if switched_plan:
        metrics_service.inc("billing_subscription_switch_total")

    log = get_request_logger(
        logger,
        request_log_context(request_id, identity.organization.id, identity.user.id),
    )
    log.info(
        "billing_webhook_processed",
        extra={
            "provider": provider,
            "external_event_id": external_event_id,
            "created": created,
            "switched_plan": switched_plan,
            "plan_code": switched_to,
        },
    )

    return {
        "status": "processed",
        "created": created,
        "switched_plan": switched_plan,
        "plan_code": switched_to,
    }
