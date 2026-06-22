from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import BillingEvent, BillingWebhookEvent, Plan, Subscription
from app.services.ledger_service import LedgerService


class BillingService:
    MONTHLY_PERIOD_DAYS = 30

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _normalize_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _next_period_end(
        cls,
        *,
        base: datetime | None = None,
        now: datetime | None = None,
        days: int | None = None,
    ) -> datetime:
        current_now = now or datetime.now(timezone.utc)
        normalized_base = cls._normalize_datetime(base)
        start = normalized_base if normalized_base and normalized_base > current_now else current_now
        return start + timedelta(days=days or cls.MONTHLY_PERIOD_DAYS)

    async def process_webhook_event(
        self,
        provider: str,
        external_event_id: str,
        payload: dict,
        organization_id: int,
    ) -> tuple[BillingWebhookEvent, bool]:
        existing_query = select(BillingWebhookEvent).where(
            and_(
                BillingWebhookEvent.provider == provider,
                BillingWebhookEvent.external_event_id == external_event_id,
            )
        )
        existing_result = await self.session.execute(existing_query)
        existing = existing_result.scalar_one_or_none()
        if existing:
            return existing, False

        webhook_event = BillingWebhookEvent(
            provider=provider,
            external_event_id=external_event_id,
            payload=payload,
        )
        self.session.add(webhook_event)

        billing_event = BillingEvent(
            organization_id=organization_id,
            event_type=payload.get("type", "billing.webhook"),
            provider=provider,
            provider_event_id=external_event_id,
            amount=float(payload.get("amount", 0.0) or 0.0),
            currency=str(payload.get("currency", "USD")),
            payload=payload,
        )
        self.session.add(billing_event)
        await self.session.commit()
        return webhook_event, True

    async def has_billing_event(
        self,
        *,
        organization_id: int,
        provider: str,
        provider_event_id: str | None,
        event_type: str,
    ) -> bool:
        if not provider_event_id:
            return False
        result = await self.session.execute(
            select(BillingEvent.id).where(
                and_(
                    BillingEvent.organization_id == organization_id,
                    BillingEvent.provider == provider,
                    BillingEvent.provider_event_id == provider_event_id,
                    BillingEvent.event_type == event_type,
                )
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    def add_billing_event(
        self,
        *,
        organization_id: int,
        event_type: str,
        provider: str | None,
        provider_event_id: str | None,
        amount: float = 0.0,
        currency: str = "USD",
        payload: dict,
    ) -> BillingEvent:
        event = BillingEvent(
            organization_id=organization_id,
            event_type=event_type,
            provider=provider,
            provider_event_id=provider_event_id,
            amount=amount,
            currency=currency,
            payload=payload,
        )
        self.session.add(event)
        return event

    async def resolve_plan(self, plan_code: str) -> Plan | None:
        normalized = (plan_code or "").strip().lower()
        if normalized == "full":
            normalized = "pro"
        if not normalized:
            return None
        result = await self.session.execute(
            select(Plan)
            .where(and_(Plan.code == normalized, Plan.is_active == True))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_subscription(self, organization_id: int) -> Subscription | None:
        result = await self.session.execute(
            select(Subscription)
            .where(
                and_(
                    Subscription.organization_id == organization_id,
                    Subscription.status == "active",
                )
            )
            .order_by(Subscription.updated_at.desc(), Subscription.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def switch_subscription_plan(
        self,
        *,
        organization_id: int,
        plan_code: str,
        paid_period_days: int | None = None,
        preserve_period_end: bool = True,
    ) -> bool:
        plan = await self.resolve_plan(plan_code)
        if plan is None:
            raise ValueError("Plan not found")

        current = await self.get_active_subscription(organization_id)
        current_period_end = self._normalize_datetime(
            current.current_period_end if current else None
        )
        now = datetime.now(timezone.utc)
        should_extend = bool(paid_period_days and float(plan.price_monthly or 0.0) > 0)

        if current and current.plan_id == plan.id:
            if should_extend:
                current.current_period_end = self._next_period_end(
                    base=current_period_end,
                    now=now,
                    days=paid_period_days,
                )
                await self.session.commit()
                return True
            return False

        if current:
            current.status = "replaced"

        next_period_end: datetime | None = None
        if should_extend:
            next_period_end = self._next_period_end(now=now, days=paid_period_days)
        elif preserve_period_end and current_period_end and current_period_end > now:
            next_period_end = current_period_end

        self.session.add(
            Subscription(
                organization_id=organization_id,
                plan_id=plan.id,
                status="active",
                current_period_end=next_period_end,
            )
        )
        await self.session.commit()
        return True

    async def reconcile_subscription(
        self,
        organization_id: int,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = await self.get_active_subscription(organization_id)
        if current is None:
            return {"action": "missing"}

        period_end = self._normalize_datetime(current.current_period_end)
        current_now = now or datetime.now(timezone.utc)
        if period_end is None or period_end > current_now:
            return {"action": "unchanged"}

        plan_result = await self.session.execute(
            select(Plan).where(Plan.id == current.plan_id).limit(1)
        )
        plan = plan_result.scalar_one_or_none()
        if plan is None:
            return {"action": "missing_plan"}

        price = round(float(plan.price_monthly or 0.0), 2)
        if price <= 0:
            return {"action": "unchanged"}

        ledger = LedgerService(self.session)
        balance = await ledger.get_balance(organization_id)
        if balance >= price:
            source_id = f"subscription:{current.id}:{period_end.isoformat()}"
            if not await ledger.has_source_entry(
                organization_id=organization_id,
                source_type="subscription_renewal",
                source_id=source_id,
            ):
                await ledger.add_entry(
                    organization_id=organization_id,
                    entry_type="debit",
                    amount=price,
                    currency=str(plan.currency or "USD"),
                    source_type="subscription_renewal",
                    source_id=source_id,
                    note=f"Auto-renew {plan.code} plan",
                    metadata={"plan_code": plan.code, "subscription_id": current.id},
                )
            current.current_period_end = self._next_period_end(
                base=period_end,
                now=current_now,
            )
            self.add_billing_event(
                organization_id=organization_id,
                event_type="subscription.renewed",
                provider="internal",
                provider_event_id=source_id,
                amount=price,
                currency=str(plan.currency or "USD"),
                payload={
                    "plan_code": plan.code,
                    "subscription_id": current.id,
                    "previous_period_end": period_end.isoformat(),
                    "current_period_end": current.current_period_end.isoformat(),
                },
            )
            await self.session.commit()
            return {"action": "renewed", "plan_code": plan.code}

        free_plan = await self.resolve_plan("free")
        if free_plan is None:
            return {"action": "free_plan_missing"}

        current.status = "expired"
        self.session.add(
            Subscription(
                organization_id=organization_id,
                plan_id=free_plan.id,
                status="active",
            )
        )
        self.add_billing_event(
            organization_id=organization_id,
            event_type="subscription.expired",
            provider="internal",
            provider_event_id=f"subscription:{current.id}:expired:{period_end.isoformat()}",
            amount=0.0,
            currency=str(plan.currency or "USD"),
            payload={
                "plan_code": plan.code,
                "subscription_id": current.id,
                "period_end": period_end.isoformat(),
                "balance": balance,
                "required_amount": price,
                "downgraded_to": "free",
            },
        )
        await self.session.commit()
        return {"action": "downgraded", "plan_code": "free"}
