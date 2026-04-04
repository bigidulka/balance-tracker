from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import BillingEvent, Plan, PromoCode, PromoRedemption
from app.services.billing_service import BillingService
from app.services.ledger_service import LedgerService


class PromoServiceError(RuntimeError):
    pass


class PromoService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def redeem_code(
        self,
        *,
        organization_id: int,
        user_id: int | None,
        raw_code: str,
    ) -> PromoRedemption:
        code = (raw_code or "").strip().upper()
        if not code:
            raise PromoServiceError("Promo code is empty")

        result = await self.session.execute(
            select(PromoCode).where(PromoCode.code == code).limit(1)
        )
        promo = result.scalar_one_or_none()
        if promo is None or not promo.is_active:
            raise PromoServiceError("Promo code not found")

        now = datetime.now(timezone.utc)
        if promo.expires_at and promo.expires_at.astimezone(timezone.utc) < now:
            raise PromoServiceError("Promo code expired")
        if promo.max_redemptions is not None and promo.redeemed_count >= promo.max_redemptions:
            raise PromoServiceError("Promo code usage limit reached")

        existing = await self.session.execute(
            select(PromoRedemption)
            .where(
                and_(
                    PromoRedemption.promo_code_id == promo.id,
                    PromoRedemption.organization_id == organization_id,
                    PromoRedemption.status == "applied",
                )
            )
            .limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            raise PromoServiceError("Promo code already applied")

        reward_snapshot: dict[str, object] = {
            "reward_type": promo.reward_type,
            "reward_value": float(promo.reward_value or 0.0),
            "reward_currency": promo.reward_currency,
            "plan_code": promo.plan_code,
            "duration_days": promo.duration_days,
        }

        if promo.reward_type == "balance_credit":
            await LedgerService(self.session).add_entry(
                organization_id=organization_id,
                created_by_user_id=user_id,
                entry_type="credit",
                amount=float(promo.reward_value or 0.0),
                currency=str(promo.reward_currency or "USD"),
                source_type="promo",
                source_id=str(promo.id),
                note=f"Promo code {promo.code}",
                metadata=reward_snapshot,
            )
        elif promo.reward_type == "plan_upgrade":
            if not promo.plan_code:
                raise PromoServiceError("Promo plan is not configured")
            changed = await BillingService(self.session).switch_subscription_plan(
                organization_id=organization_id,
                plan_code=promo.plan_code,
            )
            reward_snapshot["subscription_changed"] = changed
            if promo.duration_days:
                await self._extend_active_subscription_period(
                    organization_id=organization_id,
                    duration_days=promo.duration_days,
                )
        else:
            raise PromoServiceError("Unsupported promo type")

        redemption = PromoRedemption(
            promo_code_id=promo.id,
            organization_id=organization_id,
            user_id=user_id,
            status="applied",
            reward_snapshot=reward_snapshot,
        )
        promo.redeemed_count += 1
        self.session.add(redemption)
        self.session.add(
            BillingEvent(
                organization_id=organization_id,
                event_type="promo.redeemed",
                provider="promo",
                provider_event_id=promo.code,
                amount=float(promo.reward_value or 0.0),
                currency=str(promo.reward_currency or "USD"),
                payload=reward_snapshot,
            )
        )
        await self.session.commit()
        await self.session.refresh(redemption)
        return redemption

    async def create_promo_code(
        self,
        *,
        code: str,
        reward_type: str,
        reward_value: float,
        reward_currency: str = "USD",
        plan_code: str | None = None,
        duration_days: int | None = None,
        max_redemptions: int | None = None,
        per_org_limit: int = 1,
        expires_at: datetime | None = None,
        metadata: dict | None = None,
    ) -> PromoCode:
        normalized_code = (code or "").strip().upper()
        if not normalized_code:
            raise PromoServiceError("Promo code is empty")
        promo = PromoCode(
            code=normalized_code,
            reward_type=(reward_type or "").strip().lower(),
            reward_value=float(reward_value or 0.0),
            reward_currency=(reward_currency or "USD").upper(),
            plan_code=(plan_code or "").strip().lower() or None,
            duration_days=duration_days,
            max_redemptions=max_redemptions,
            per_org_limit=max(1, int(per_org_limit or 1)),
            expires_at=expires_at,
            metadata_json=dict(metadata or {}),
        )
        if promo.reward_type == "plan_upgrade" and promo.plan_code:
            result = await self.session.execute(
                select(Plan).where(and_(Plan.code == promo.plan_code, Plan.is_active == True)).limit(1)
            )
            if result.scalar_one_or_none() is None:
                raise PromoServiceError("Plan for promo code not found")
        self.session.add(promo)
        await self.session.commit()
        await self.session.refresh(promo)
        return promo

    async def list_promo_codes(self, *, limit: int = 50) -> list[PromoCode]:
        result = await self.session.execute(
            select(PromoCode)
            .order_by(PromoCode.created_at.desc(), PromoCode.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(result.scalars().all())

    async def _extend_active_subscription_period(
        self,
        *,
        organization_id: int,
        duration_days: int,
    ) -> None:
        subscription = await BillingService(self.session).get_active_subscription(organization_id)
        if subscription is None:
            return
        base = subscription.current_period_end or datetime.now(timezone.utc)
        subscription.current_period_end = base + timedelta(days=max(1, duration_days))
        await self.session.commit()
