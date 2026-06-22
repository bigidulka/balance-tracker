import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import IdentityContext, require_role
from app.models.balance import PaymentInvoice, Plan
from app.schemas.billing import (
    BillingBalanceResponse,
    BillingPlanSchema,
    BillingPlansResponse,
    CreateInvoiceRequest,
    CurrentSubscriptionResponse,
    InvoiceListResponse,
    InvoiceSchema,
    RedeemPromoRequest,
    RedeemPromoResponse,
    SwitchPlanRequest,
    SwitchPlanResponse,
)
from app.services.entitlements_service import EntitlementsService
from app.services.audit_log_service import AuditLogService
from app.services.billing_service import BillingService
from app.services.crypto_bot_service import CryptoBotService, CryptoBotServiceError
from app.services.ledger_service import LedgerService
from app.services.logging_context import get_request_logger, request_log_context
from app.services.metrics_service import metrics_service
from app.services.promo_service import PromoService, PromoServiceError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
settings = get_settings()


async def _resolve_target_plan(
    db: AsyncSession,
    payload: dict[str, Any],
) -> Plan | None:
    plan_code = payload.get("plan_code") or payload.get("plan") or payload.get("tier")
    if not isinstance(plan_code, str) or not plan_code.strip():
        return None

    normalized = plan_code.strip().lower()
    if normalized == "full":
        normalized = "pro"

    if normalized not in {"free", "low", "medium", "pro"}:
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
    return await BillingService(db).switch_subscription_plan(
        organization_id=organization_id,
        plan_code=plan.code,
    )


def _invoice_schema(invoice: PaymentInvoice) -> InvoiceSchema:
    metadata = invoice.metadata_json if isinstance(invoice.metadata_json, dict) else {}
    plan_code = metadata.get("plan_code")
    if not isinstance(plan_code, str) or not plan_code.strip():
        plan_code = None
    return InvoiceSchema(
        id=invoice.id,
        status=invoice.status,
        invoice_type=str(invoice.invoice_type or "balance_topup"),
        amount=float(invoice.amount or 0.0),
        currency=str(invoice.currency or "USD"),
        asset=invoice.asset,
        plan_code=plan_code,
        pay_url=invoice.pay_url,
        bot_invoice_url=invoice.bot_invoice_url,
        external_invoice_id=invoice.external_invoice_id,
        description=invoice.description,
        paid_amount=float(invoice.paid_amount) if invoice.paid_amount is not None else None,
        paid_asset=invoice.paid_asset,
        paid_usd_amount=float(invoice.paid_usd_amount) if invoice.paid_usd_amount is not None else None,
        expires_at=invoice.expires_at.isoformat() if invoice.expires_at else None,
        paid_at=invoice.paid_at.isoformat() if invoice.paid_at else None,
        created_at=invoice.created_at.isoformat() if invoice.created_at else None,
    )


@router.get("/plans", response_model=BillingPlansResponse)
async def list_billing_plans(
    db: AsyncSession = Depends(get_db),
    _: IdentityContext = Depends(require_role("viewer")),
):
    service = EntitlementsService(db)
    await service.ensure_default_plans()
    plans = await service.list_active_plans()
    payload = []
    for plan in plans:
        policy = service._resolve_policy(plan)
        payload.append(
            BillingPlanSchema(
                code=plan.code,
                name=plan.name,
                price_monthly=float(plan.price_monthly or 0.0),
                currency=str(plan.currency or "USD"),
                policy=policy,
                is_active=bool(plan.is_active),
            )
        )
    return BillingPlansResponse(plans=payload)


@router.get("/current", response_model=CurrentSubscriptionResponse)
async def get_current_subscription(
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("viewer")),
):
    service = EntitlementsService(db)
    await service.ensure_default_plans()
    await service.ensure_default_subscription(identity.organization.id)
    entitlements = await service.get_effective_entitlements(identity.organization.id)
    billing = BillingService(db)
    subscription = await billing.get_active_subscription(identity.organization.id)
    ledger = LedgerService(db)
    balance = await ledger.get_balance(identity.organization.id)
    entries = await ledger.list_entries(identity.organization.id, limit=5)
    return CurrentSubscriptionResponse(
        **entitlements,
        subscription={
            "id": subscription.id if subscription else None,
            "status": subscription.status if subscription else None,
            "current_period_end": subscription.current_period_end.isoformat()
            if subscription and subscription.current_period_end
            else None,
            "cancel_at_period_end": bool(subscription.cancel_at_period_end)
            if subscription
            else False,
        },
        wallet={
            "currency": "USD",
            "available": balance,
            "entries": [
                {
                    "id": entry.id,
                    "type": entry.entry_type,
                    "amount": float(entry.amount or 0.0),
                    "source_type": entry.source_type,
                    "created_at": entry.created_at.isoformat() if entry.created_at else None,
                }
                for entry in entries
            ],
        },
    )


@router.post("/switch", response_model=SwitchPlanResponse)
async def switch_subscription_plan(
    payload: SwitchPlanRequest,
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("member")),
):
    service = EntitlementsService(db)
    await service.ensure_default_plans()
    await service.ensure_default_subscription(identity.organization.id)
    target_plan = await _resolve_target_plan(db, {"plan_code": payload.plan_code})
    if target_plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    changed = await _switch_subscription_if_needed(
        db,
        organization_id=identity.organization.id,
        plan=target_plan,
    )
    return SwitchPlanResponse(
        status="ok",
        changed=changed,
        plan_code=target_plan.code,
    )


@router.get("/balance", response_model=BillingBalanceResponse)
async def get_wallet_balance(
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("viewer")),
):
    ledger = LedgerService(db)
    entries = await ledger.list_entries(identity.organization.id, limit=10)
    return BillingBalanceResponse(
        currency="USD",
        available=await ledger.get_balance(identity.organization.id),
        entries=[
            {
                "id": entry.id,
                "type": entry.entry_type,
                "amount": float(entry.amount or 0.0),
                "source_type": entry.source_type,
                "note": entry.note,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
            }
            for entry in entries
        ],
    )


@router.get("/invoices", response_model=InvoiceListResponse)
async def list_payment_invoices(
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("viewer")),
):
    service = CryptoBotService(db)
    try:
        items = await service.list_invoices(identity.organization.id, limit=10)
        return InvoiceListResponse(items=[_invoice_schema(item) for item in items])
    finally:
        await service.close()


@router.post("/invoices", response_model=InvoiceSchema)
async def create_payment_invoice(
    payload: CreateInvoiceRequest,
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("member")),
):
    entitlements = EntitlementsService(db)
    await entitlements.ensure_default_plans()
    await entitlements.ensure_default_subscription(identity.organization.id)
    if payload.invoice_type == "plan_purchase":
        target_plan = await _resolve_target_plan(db, {"plan_code": payload.plan_code})
        if target_plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        if float(target_plan.price_monthly or 0.0) <= 0:
            await _switch_subscription_if_needed(
                db,
                organization_id=identity.organization.id,
                plan=target_plan,
            )
            return InvoiceSchema(
                id=0,
                status="switched",
                invoice_type="plan_purchase",
                amount=float(target_plan.price_monthly or 0.0),
                currency=str(target_plan.currency or "USD"),
                plan_code=target_plan.code,
                description=f"Plan switched to {target_plan.name}",
            )
    if not settings.crypto_bot_api_token:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "payment_provider_unavailable",
                "message": "Payment provider is not configured",
            },
        )

    service = CryptoBotService(db)
    try:
        if payload.invoice_type == "plan_purchase":
            invoice = await service.create_plan_purchase_invoice(
                organization_id=identity.organization.id,
                user_id=identity.user.id,
                plan_code=payload.plan_code or "",
                amount_usd=payload.amount_usd,
            )
        else:
            invoice = await service.create_balance_topup_invoice(
                organization_id=identity.organization.id,
                user_id=identity.user.id,
                amount_usd=payload.amount_usd or 0.0,
            )
    except CryptoBotServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await service.close()
    return _invoice_schema(invoice)


@router.post("/invoices/{invoice_id}/refresh", response_model=InvoiceSchema)
async def refresh_payment_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("viewer")),
):
    service = CryptoBotService(db)
    try:
        invoice = await service.get_invoice_by_id(invoice_id, identity.organization.id)
        if invoice is None:
            raise HTTPException(status_code=404, detail="Invoice not found")
        refreshed = await service.refresh_invoice_status(invoice)
    except CryptoBotServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await service.close()
    return _invoice_schema(refreshed)


@router.post("/promo/redeem", response_model=RedeemPromoResponse)
async def redeem_promo_code(
    payload: RedeemPromoRequest,
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("member")),
):
    service = PromoService(db)
    try:
        redemption = await service.redeem_code(
            organization_id=identity.organization.id,
            user_id=identity.user.id,
            raw_code=payload.code,
        )
    except PromoServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reward = redemption.reward_snapshot if isinstance(redemption.reward_snapshot, dict) else {}
    return RedeemPromoResponse(
        status=redemption.status,
        reward_type=str(reward.get("reward_type") or ""),
        reward_value=float(reward.get("reward_value") or 0.0),
        reward_currency=str(reward.get("reward_currency") or "USD"),
        plan_code=str(reward.get("plan_code")) if reward.get("plan_code") else None,
    )


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

    entitlements = EntitlementsService(db)
    await entitlements.ensure_default_plans()
    service = BillingService(db)
    event, created = await service.process_webhook_event(
        provider=provider,
        external_event_id=external_event_id,
        payload=payload,
        organization_id=identity.organization.id,
    )

    switched_plan = False
    switched_to: str | None = None
    if provider == "cryptobot":
        crypto_service = CryptoBotService(db)
        try:
            invoice = await crypto_service.apply_webhook_update(
                payload=payload,
                external_event_id=external_event_id,
            )
            if invoice is not None and invoice.status == "paid":
                switched_plan = invoice.invoice_type == "plan_purchase"
                switched_to = crypto_service._extract_plan_code(invoice)
        except CryptoBotServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await crypto_service.close()
    elif created:
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


@router.post("/cryptobot/webhook")
async def cryptobot_webhook(
    payload: dict[str, Any],
    x_event_id: str | None = Header(default=None, alias="x-event-id"),
    crypto_pay_api_secret_token: str | None = Header(
        default=None,
        alias="crypto-pay-api-secret-token",
    ),
    x_crypto_bot_secret: str | None = Header(default=None, alias="x-crypto-bot-secret"),
    db: AsyncSession = Depends(get_db),
):
    secret = crypto_pay_api_secret_token or x_crypto_bot_secret
    if settings.crypto_bot_webhook_secret and secret != settings.crypto_bot_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    event_id = x_event_id or str(payload.get("update_id") or payload.get("invoice_id") or "")
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event id")

    invoice_service = CryptoBotService(db)
    try:
        invoice = await invoice_service.apply_webhook_update(payload=payload, external_event_id=event_id)
    finally:
        await invoice_service.close()
    organization_id = invoice.organization_id if invoice is not None else 1
    event, created = await BillingService(db).process_webhook_event(
        provider="cryptobot",
        external_event_id=event_id,
        payload=payload,
        organization_id=organization_id,
    )
    return {
        "status": "processed",
        "created": created,
        "invoice_id": invoice.id if invoice else None,
        "event_id": event.id,
    }
