from __future__ import annotations

from collections import defaultdict

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import (
    Integration,
    LedgerEntry,
    Organization,
    OrganizationMembership,
    Plan,
    Subscription,
    TelegramIdentity,
    User,
)


class AdminRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _visible_membership_clause():
        return or_(
            TelegramIdentity.id.is_not(None),
            ~User.email.like("api-user-%@local.invalid"),
        )

    async def list_user_memberships(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        total_result = await self.session.execute(
            select(func.count(OrganizationMembership.id))
            .select_from(OrganizationMembership)
            .join(User, User.id == OrganizationMembership.user_id)
            .outerjoin(TelegramIdentity, TelegramIdentity.user_id == User.id)
            .where(self._visible_membership_clause())
        )
        total = int(total_result.scalar_one() or 0)

        rows = await self.session.execute(
            select(OrganizationMembership, User, Organization, TelegramIdentity)
            .join(User, User.id == OrganizationMembership.user_id)
            .join(Organization, Organization.id == OrganizationMembership.organization_id)
            .outerjoin(TelegramIdentity, TelegramIdentity.user_id == User.id)
            .where(self._visible_membership_clause())
            .order_by(OrganizationMembership.id.desc())
            .limit(limit)
            .offset(offset)
        )
        records = rows.all()
        org_ids = [organization.id for _, _, organization, _ in records]
        aggregates = await self._load_org_aggregates(org_ids)

        items: list[dict] = []
        for membership, user, organization, telegram_identity in records:
            telegram_user_id = (
                telegram_identity.telegram_user_id
                if telegram_identity is not None
                else getattr(user, "telegram_user_id", None)
            )
            telegram_username = (
                telegram_identity.telegram_username
                if telegram_identity is not None
                else getattr(user, "telegram_username", None)
            )
            telegram_full_name = (
                telegram_identity.telegram_full_name
                if telegram_identity is not None
                else getattr(user, "telegram_full_name", None)
            )
            org_meta = aggregates.get(organization.id, {})
            items.append(
                {
                    "membership_id": membership.id,
                    "organization_id": organization.id,
                    "organization_name": organization.name,
                    "user_id": user.id,
                    "email": user.email,
                    "full_name": user.full_name,
                    "telegram_user_id": telegram_user_id,
                    "telegram_username": telegram_username,
                    "telegram_full_name": telegram_full_name,
                    "role": membership.role,
                    "is_active": bool(user.is_active and organization.is_active),
                    "balance_usd": float(org_meta.get("balance_usd") or 0.0),
                    "integrations_total": int(org_meta.get("integrations_total") or 0),
                    "active_integrations": int(org_meta.get("active_integrations") or 0),
                    "plan_code": str(org_meta.get("plan_code") or "free"),
                    "plan_name": str(org_meta.get("plan_name") or "Free"),
                    "created_at": user.created_at,
                }
            )
        return items, total

    async def get_user_membership_detail(
        self,
        *,
        user_id: int,
        organization_id: int,
    ) -> dict | None:
        row = await self.session.execute(
            select(OrganizationMembership, User, Organization, TelegramIdentity)
            .join(User, User.id == OrganizationMembership.user_id)
            .join(Organization, Organization.id == OrganizationMembership.organization_id)
            .outerjoin(TelegramIdentity, TelegramIdentity.user_id == User.id)
            .where(
                and_(
                    OrganizationMembership.user_id == user_id,
                    OrganizationMembership.organization_id == organization_id,
                    self._visible_membership_clause(),
                )
            )
            .limit(1)
        )
        record = row.first()
        if record is None:
            return None
        membership, user, organization, telegram_identity = record
        telegram_user_id = (
            telegram_identity.telegram_user_id
            if telegram_identity is not None
            else getattr(user, "telegram_user_id", None)
        )
        telegram_username = (
            telegram_identity.telegram_username
            if telegram_identity is not None
            else getattr(user, "telegram_username", None)
        )
        telegram_full_name = (
            telegram_identity.telegram_full_name
            if telegram_identity is not None
            else getattr(user, "telegram_full_name", None)
        )
        aggregates = await self._load_org_aggregates([organization.id])
        integrations = await self.session.execute(
            select(Integration)
            .where(Integration.organization_id == organization.id)
            .order_by(Integration.created_at.desc(), Integration.id.desc())
        )
        org_meta = aggregates.get(organization.id, {})
        return {
            "membership_id": membership.id,
            "organization_id": organization.id,
            "organization_name": organization.name,
            "user_id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "telegram_user_id": telegram_user_id,
            "telegram_username": telegram_username,
            "telegram_full_name": telegram_full_name,
            "role": membership.role,
            "is_active": bool(user.is_active and organization.is_active),
            "balance_usd": float(org_meta.get("balance_usd") or 0.0),
            "integrations_total": int(org_meta.get("integrations_total") or 0),
            "active_integrations": int(org_meta.get("active_integrations") or 0),
            "plan_code": str(org_meta.get("plan_code") or "free"),
            "plan_name": str(org_meta.get("plan_name") or "Free"),
            "created_at": user.created_at,
            "integrations": [
                {
                    "id": item.id,
                    "name": item.name,
                    "provider": item.provider,
                    "kind": item.kind,
                    "exchange_code": item.exchange_code,
                    "wallet_address": item.wallet_address,
                    "chain": item.chain,
                    "is_active": item.is_active,
                }
                for item in integrations.scalars().all()
            ],
        }

    async def _load_org_aggregates(self, organization_ids: list[int]) -> dict[int, dict]:
        if not organization_ids:
            return {}

        result: dict[int, dict] = defaultdict(dict)

        integrations = await self.session.execute(
            select(
                Integration.organization_id,
                func.count(Integration.id),
                func.sum(case((Integration.is_active == True, 1), else_=0)),
            )
            .where(Integration.organization_id.in_(organization_ids))
            .group_by(Integration.organization_id)
        )
        for organization_id, total, active in integrations.all():
            result[int(organization_id)]["integrations_total"] = int(total or 0)
            result[int(organization_id)]["active_integrations"] = int(active or 0)

        subscriptions = await self.session.execute(
            select(Subscription.organization_id, Plan.code, Plan.name)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Subscription.organization_id.in_(organization_ids),
                Subscription.status == "active",
            )
            .order_by(Subscription.updated_at.desc(), Subscription.id.desc())
        )
        for organization_id, plan_code, plan_name in subscriptions.all():
            if "plan_code" in result[int(organization_id)]:
                continue
            result[int(organization_id)]["plan_code"] = str(plan_code or "free")
            result[int(organization_id)]["plan_name"] = str(plan_name or "Free")

        entries = await self.session.execute(
            select(
                LedgerEntry.organization_id,
                func.sum(
                    case(
                        (LedgerEntry.entry_type.in_(("credit", "release")), LedgerEntry.amount),
                        else_=-LedgerEntry.amount,
                    )
                ),
            )
            .where(LedgerEntry.organization_id.in_(organization_ids))
            .group_by(LedgerEntry.organization_id)
        )
        for organization_id, balance_value in entries.all():
            result[int(organization_id)]["balance_usd"] = float(balance_value or 0.0)

        return result
