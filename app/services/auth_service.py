from dataclasses import dataclass

from app.core.config import get_settings
from app.core.security import (
    SecurityError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models.balance import Organization, OrganizationMembership, TelegramIdentity, User
from app.repositories.auth import AuthRepository

settings = get_settings()


class AuthServiceError(Exception):
    pass


@dataclass
class AuthenticatedIdentity:
    user: User
    organization: Organization
    membership: OrganizationMembership


@dataclass
class TelegramBootstrapResult:
    access_token: str
    user: User
    organization: Organization
    membership: OrganizationMembership
    telegram_identity: TelegramIdentity
    is_platform_admin: bool


class AuthService:
    def __init__(self, repo: AuthRepository):
        self.repo = repo

    async def register(self, email: str, password: str, full_name: str | None = None) -> User:
        existing = await self.repo.get_user_by_email(email)
        if existing is not None:
            raise AuthServiceError("User with this email already exists")

        password_hash = hash_password(password)
        user = await self.repo.create_user(
            email=email,
            password_hash=password_hash,
            full_name=full_name,
        )

        default_org = await self.repo.ensure_default_organization()
        membership = await self.repo.get_membership(user.id, default_org.id)
        if membership is None:
            await self.repo.add_membership(user.id, default_org.id, role="owner")

        return user

    async def login(self, email: str, password: str, organization_id: int | None = None) -> str:
        user = await self.repo.get_user_by_email(email)
        if user is None or not user.is_active:
            raise AuthServiceError("Invalid credentials")

        if not verify_password(password, user.password_hash):
            raise AuthServiceError("Invalid credentials")

        target_org_id = await self.resolve_organization_for_user(user.id, organization_id)
        return create_access_token(user_id=user.id, organization_id=target_org_id)

    async def get_user(self, user_id: int) -> User:
        user = await self.repo.get_user_by_id(user_id)
        if user is None or not user.is_active:
            raise AuthServiceError("User is not active")
        return user

    async def resolve_organization_for_user(
        self, user_id: int, organization_id: int | None = None
    ) -> int:
        if organization_id is not None:
            membership = await self.repo.get_membership(user_id, organization_id)
            if membership is None:
                raise AuthServiceError("User is not a member of the organization")
            return organization_id

        memberships = await self.repo.get_user_memberships(user_id)
        if memberships:
            return memberships[0].organization_id

        default_org = await self.repo.ensure_default_organization()
        await self.repo.add_membership(user_id, default_org.id, role="member")
        return default_org.id

    async def resolve_identity(
        self, token: str, organization_override: int | None = None
    ) -> AuthenticatedIdentity:
        try:
            payload = decode_access_token(token)
        except SecurityError as exc:
            raise AuthServiceError(str(exc)) from exc

        sub = payload.get("sub")
        if sub is None:
            raise AuthServiceError("Token subject is missing")

        try:
            user_id = int(sub)
        except ValueError as exc:
            raise AuthServiceError("Token subject is invalid") from exc

        user = await self.get_user(user_id)

        token_org = payload.get("org")
        token_org_id = None
        if token_org is not None:
            try:
                token_org_id = int(token_org)
            except (TypeError, ValueError) as exc:
                raise AuthServiceError("Token organization is invalid") from exc
        organization_id = await self.resolve_organization_for_user(
            user.id, organization_override or token_org_id
        )

        organization = await self.repo.get_organization_by_id(organization_id)
        if organization is None or not organization.is_active:
            raise AuthServiceError("Organization is not active")

        membership = await self.repo.get_membership(user.id, organization.id)
        if membership is None:
            raise AuthServiceError("Membership not found")

        return AuthenticatedIdentity(
            user=user,
            organization=organization,
            membership=membership,
        )

    async def bootstrap_telegram_identity(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None = None,
        telegram_first_name: str | None = None,
        telegram_last_name: str | None = None,
        telegram_full_name: str | None = None,
    ) -> TelegramBootstrapResult:
        telegram_user_id = int(telegram_user_id)
        full_name = self._resolve_display_name(
            telegram_full_name=telegram_full_name,
            telegram_first_name=telegram_first_name,
            telegram_last_name=telegram_last_name,
        )
        is_platform_admin = telegram_user_id == settings.telegram_admin_user_id
        identity = await self.repo.get_telegram_identity(telegram_user_id)
        if identity is not None:
            user = await self.repo.get_user_by_id(identity.user_id)
            organization = await self.repo.get_organization_by_id(identity.organization_id)
            membership = await self.repo.get_membership(identity.user_id, identity.organization_id)
            if user is None or organization is None or membership is None:
                raise AuthServiceError("Telegram identity is inconsistent")
            await self._update_telegram_identity(
                identity=identity,
                telegram_username=telegram_username,
                telegram_first_name=telegram_first_name,
                telegram_last_name=telegram_last_name,
                telegram_full_name=full_name,
                user=user,
            )
            user.telegram_user_id = telegram_user_id
            user.telegram_username = telegram_username or user.telegram_username
            user.telegram_full_name = full_name or user.telegram_full_name
            role = "owner" if is_platform_admin else "member"
            if membership.role != role:
                membership.role = role
            await self.repo.session.commit()
            await self.repo.session.refresh(user)
            await self.repo.session.refresh(membership)
            token = create_access_token(user_id=user.id, organization_id=organization.id)
            return TelegramBootstrapResult(
                access_token=token,
                user=user,
                organization=organization,
                membership=membership,
                telegram_identity=identity,
                is_platform_admin=is_platform_admin,
            )

        organization = await self._create_personal_organization(telegram_user_id)
        user = await self.repo.get_user_by_telegram_user_id(telegram_user_id)
        if user is None:
            user = await self.repo.get_user_by_email(self._telegram_email(telegram_user_id))
        if user is None:
            user = await self.repo.create_user(
                email=self._telegram_email(telegram_user_id),
                password_hash="!",
                full_name=full_name,
            )
        user.telegram_user_id = telegram_user_id
        user.telegram_username = telegram_username
        user.telegram_full_name = full_name
        if full_name and user.full_name != full_name:
            user.full_name = full_name
        await self.repo.session.commit()
        await self.repo.session.refresh(user)
        role = "owner" if is_platform_admin else "member"
        membership = await self.repo.get_membership(user.id, organization.id)
        if membership is None:
            membership = await self.repo.add_membership(user.id, organization.id, role=role)
        elif membership.role != role:
            membership.role = role
            await self.repo.session.commit()
            await self.repo.session.refresh(membership)
        identity = await self.repo.create_telegram_identity(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            telegram_first_name=telegram_first_name,
            telegram_last_name=telegram_last_name,
            telegram_full_name=full_name,
            user=user,
            organization=organization,
        )
        await self.repo.session.commit()
        await self.repo.session.refresh(identity)
        token = create_access_token(user_id=user.id, organization_id=organization.id)
        return TelegramBootstrapResult(
            access_token=token,
            user=user,
            organization=organization,
            membership=membership,
            telegram_identity=identity,
            is_platform_admin=is_platform_admin,
        )

    async def _create_personal_organization(self, telegram_user_id: int) -> Organization:
        existing = await self.repo.get_organization_by_telegram_user_id(telegram_user_id)
        if existing is not None:
            return existing
        return await self.repo.create_organization(
            name=f"Telegram {telegram_user_id}",
            slug=f"tg-{telegram_user_id}",
        )

    async def _update_telegram_identity(
        self,
        *,
        identity: TelegramIdentity,
        telegram_username: str | None,
        telegram_first_name: str | None,
        telegram_last_name: str | None,
        telegram_full_name: str | None,
        user: User,
    ) -> None:
        changed = False
        if identity.telegram_username != telegram_username:
            identity.telegram_username = telegram_username
            changed = True
        if identity.telegram_first_name != telegram_first_name:
            identity.telegram_first_name = telegram_first_name
            changed = True
        if identity.telegram_last_name != telegram_last_name:
            identity.telegram_last_name = telegram_last_name
            changed = True
        if identity.telegram_full_name != telegram_full_name:
            identity.telegram_full_name = telegram_full_name
            changed = True
        if telegram_full_name and user.full_name != telegram_full_name:
            user.full_name = telegram_full_name
            changed = True
        if changed:
            await self.repo.session.commit()
            await self.repo.session.refresh(identity)
            await self.repo.session.refresh(user)

    @staticmethod
    def _resolve_display_name(
        *,
        telegram_full_name: str | None,
        telegram_first_name: str | None,
        telegram_last_name: str | None,
    ) -> str | None:
        if telegram_full_name and telegram_full_name.strip():
            return telegram_full_name.strip()
        parts = [part.strip() for part in (telegram_first_name, telegram_last_name) if part and part.strip()]
        if parts:
            return " ".join(parts)
        return None

    @staticmethod
    def _telegram_email(telegram_user_id: int) -> str:
        return f"telegram-user-{telegram_user_id}@local.invalid"
