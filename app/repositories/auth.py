from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.balance import Organization, OrganizationMembership, Plan, Subscription, User

settings = get_settings()


class AuthRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_email(self, email: str) -> Optional[User]:
        query = select(User).where(User.email == email.lower())
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        query = select(User).where(User.id == user_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_user(self, email: str, password_hash: str, full_name: str | None) -> User:
        user = User(email=email.lower(), password_hash=password_hash, full_name=full_name)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def get_organization_by_id(self, organization_id: int) -> Optional[Organization]:
        query = select(Organization).where(Organization.id == organization_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_organization_by_slug(self, slug: str) -> Optional[Organization]:
        query = select(Organization).where(Organization.slug == slug)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_organization(self, name: str, slug: str) -> Organization:
        org = Organization(name=name, slug=slug)
        self.session.add(org)
        await self.session.commit()
        await self.session.refresh(org)
        return org

    async def _ensure_default_plan_and_subscription(self, organization_id: int) -> None:
        free_result = await self.session.execute(
            select(Plan).where(and_(Plan.code == "free", Plan.is_active == True)).limit(1)
        )
        free_plan = free_result.scalar_one_or_none()

        if free_plan is None:
            free_plan = Plan(
                code="free",
                name="Free",
                max_integrations=1000,
                min_refresh_interval_seconds=3600,
                policy_json={
                    "limits": {
                        "max_integrations": 1000,
                        "max_accounts_per_exchange": 1,
                    },
                    "throttling": {"min_refresh_interval_seconds": 3600},
                    "capabilities": {"allow_dex": 0},
                },
                price_monthly=0.0,
                currency="USD",
                is_active=True,
            )
            self.session.add(free_plan)
            await self.session.flush()

        existing_sub = await self.session.execute(
            select(Subscription)
            .where(
                and_(
                    Subscription.organization_id == organization_id,
                    Subscription.status == "active",
                )
            )
            .limit(1)
        )
        if existing_sub.scalar_one_or_none() is None:
            self.session.add(
                Subscription(
                    organization_id=organization_id,
                    plan_id=free_plan.id,
                    status="active",
                )
            )

        await self.session.commit()

    async def ensure_default_organization(self) -> Organization:
        org = await self.get_organization_by_id(settings.default_org_id)
        if org:
            await self._ensure_default_plan_and_subscription(org.id)
            return org

        default_slug = "default"
        existing_slug = await self.get_organization_by_slug(default_slug)
        if existing_slug:
            await self._ensure_default_plan_and_subscription(existing_slug.id)
            return existing_slug

        org = await self.create_organization(name="Default Organization", slug=default_slug)
        await self._ensure_default_plan_and_subscription(org.id)
        return org

    async def list_active_organizations(self) -> list[Organization]:
        query = select(Organization).where(Organization.is_active == True).order_by(Organization.id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_membership(
        self, user_id: int, organization_id: int
    ) -> Optional[OrganizationMembership]:
        query = select(OrganizationMembership).where(
            and_(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == organization_id,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_user_memberships(self, user_id: int) -> list[OrganizationMembership]:
        query = select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def add_membership(
        self, user_id: int, organization_id: int, role: str = "member"
    ) -> OrganizationMembership:
        membership = OrganizationMembership(
            user_id=user_id,
            organization_id=organization_id,
            role=role,
        )
        self.session.add(membership)
        await self.session.commit()
        await self.session.refresh(membership)
        return membership
