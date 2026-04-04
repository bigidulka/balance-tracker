from typing import Optional

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.balance import (
    Organization,
    OrganizationMembership,
    Plan,
    Subscription,
    TelegramIdentity,
    User,
)

settings = get_settings()


class AuthRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _sync_postgres_sequence(self, table_name: str, column_name: str = "id") -> None:
        bind = self.session.bind
        if bind is None or bind.dialect.name != "postgresql":
            return
        await self.session.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence(:table_name, :column_name),
                    COALESCE((SELECT MAX(id) FROM ONLY "{table_name}"), 1),
                    COALESCE((SELECT MAX(id) FROM ONLY "{table_name}"), 1) > 0
                )
                """.replace("{table_name}", table_name)
            ),
            {"table_name": table_name, "column_name": column_name},
        )

    async def get_user_by_email(self, email: str) -> Optional[User]:
        query = select(User).where(User.email == email.lower())
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        query = select(User).where(User.id == user_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_user_by_telegram_user_id(self, telegram_user_id: int) -> Optional[User]:
        query = select(User).where(User.telegram_user_id == telegram_user_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_user(self, email: str, password_hash: str, full_name: str | None) -> User:
        await self._sync_postgres_sequence("users")
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

    async def get_organization_by_telegram_user_id(self, telegram_user_id: int) -> Optional[Organization]:
        query = (
            select(Organization)
            .join(TelegramIdentity, TelegramIdentity.organization_id == Organization.id)
            .where(TelegramIdentity.telegram_user_id == telegram_user_id)
        )
        result = await self.session.execute(query)
        organization = result.scalar_one_or_none()
        if organization is not None:
            return organization
        return await self.get_organization_by_slug(f"tg-{telegram_user_id}")

    async def create_organization(self, name: str, slug: str) -> Organization:
        await self._sync_postgres_sequence("organizations")
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
                max_integrations=6,
                min_refresh_interval_seconds=600,
                policy_json={
                    "version": 1,
                    "features": {"allow_dex": True},
                    "limits": {
                        "max_integrations": 6,
                        "max_accounts_per_exchange": 0,
                        "max_cex_accounts": 5,
                        "max_evm_wallets": 1,
                    },
                    "background": {"enabled": True, "refresh_interval_seconds": 600},
                    "throttling": {
                        "min_refresh_interval_seconds": 600,
                        "background_refresh_interval_seconds": 600,
                    },
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

    async def get_telegram_identity(self, telegram_user_id: int) -> TelegramIdentity | None:
        result = await self.session.execute(
            select(TelegramIdentity).where(TelegramIdentity.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def create_telegram_identity(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None,
        telegram_first_name: str | None,
        telegram_last_name: str | None,
        telegram_full_name: str | None,
        user: User,
        organization: Organization,
    ) -> TelegramIdentity:
        identity = TelegramIdentity(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            telegram_first_name=telegram_first_name,
            telegram_last_name=telegram_last_name,
            telegram_full_name=telegram_full_name,
            user_id=user.id,
            organization_id=organization.id,
        )
        self.session.add(identity)
        await self.session.flush()
        return identity

    async def ensure_service_identity(
        self,
        *,
        user_id: int,
        organization_id: int,
        email: str,
        full_name: str | None = None,
        role: str = "owner",
    ) -> User:
        organization = await self.get_organization_by_id(organization_id)
        if organization is None:
            if organization_id == settings.default_org_id:
                organization = await self.ensure_default_organization()
            else:
                organization = Organization(
                    id=organization_id,
                    name=f"Organization {organization_id}",
                    slug=f"org-{organization_id}",
                    is_active=True,
                )
                self.session.add(organization)
                await self.session.flush()

        user = await self.get_user_by_id(user_id)
        if user is None:
            user = User(
                id=user_id,
                email=email.lower(),
                password_hash="!",
                full_name=full_name,
                is_active=True,
            )
            self.session.add(user)
            await self.session.flush()
        else:
            changed = False
            if not user.is_active:
                user.is_active = True
                changed = True
            if not user.email:
                user.email = email.lower()
                changed = True
            if full_name and not user.full_name:
                user.full_name = full_name
                changed = True
            if changed:
                await self.session.flush()

        membership = await self.get_membership(user.id, organization.id)
        if membership is None:
            membership = OrganizationMembership(
                user_id=user.id,
                organization_id=organization.id,
                role=role,
            )
            self.session.add(membership)

        await self.session.commit()
        await self._sync_postgres_sequence("users")
        await self._sync_postgres_sequence("organizations")
        await self.session.refresh(user)
        return user
