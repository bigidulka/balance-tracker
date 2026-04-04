from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import BillingEvent, BillingWebhookEvent, Plan, Subscription


class BillingService:
    def __init__(self, session: AsyncSession):
        self.session = session

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
    ) -> bool:
        plan = await self.resolve_plan(plan_code)
        if plan is None:
            raise ValueError("Plan not found")

        current = await self.get_active_subscription(organization_id)
        if current and current.plan_id == plan.id:
            return False

        if current:
            current.status = "replaced"

        self.session.add(
            Subscription(
                organization_id=organization_id,
                plan_id=plan.id,
                status="active",
            )
        )
        await self.session.commit()
        return True
