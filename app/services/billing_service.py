from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import BillingEvent, BillingWebhookEvent


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
