from dataclasses import dataclass

from app.core.security import (
    SecurityError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models.balance import Organization, OrganizationMembership, User
from app.repositories.auth import AuthRepository


class AuthServiceError(Exception):
    pass


@dataclass
class AuthenticatedIdentity:
    user: User
    organization: Organization
    membership: OrganizationMembership


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
        token_org_id = int(token_org) if token_org is not None else None
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
