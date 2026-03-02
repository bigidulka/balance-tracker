from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.rbac import require_admin, require_member, require_owner, require_viewer
from app.models.balance import Organization, OrganizationMembership, User
from app.repositories.auth import AuthRepository
from app.services.auth_service import AuthService, AuthServiceError

settings = get_settings()


@dataclass(slots=True)
class IdentityContext:
    user: User
    organization: Organization
    membership: OrganizationMembership


async def get_identity_context(
    authorization: str = Header(default=""),
    x_organization_id: int | None = Header(default=None, alias="X-Organization-Id"),
    organization_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> IdentityContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    token = authorization.split(" ", 1)[1].strip()
    repo = AuthRepository(db)
    service = AuthService(repo)

    try:
        identity = await service.resolve_identity(
            token,
            organization_override=x_organization_id or organization_id,
        )
    except AuthServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    return IdentityContext(
        user=identity.user,
        organization=identity.organization,
        membership=identity.membership,
    )


async def get_current_user(
    identity: IdentityContext = Depends(get_identity_context),
) -> User:
    return identity.user


async def get_current_organization_id(
    request: Request,
    identity: IdentityContext = Depends(get_identity_context),
) -> int:
    request.state.request_context.organization_id = identity.organization.id
    request.state.request_context.user_id = identity.user.id
    return identity.organization.id


def require_role(min_role: str):
    async def _checker(identity: IdentityContext = Depends(get_identity_context)) -> IdentityContext:
        role = identity.membership.role
        allowed = False
        if min_role == "owner":
            allowed = require_owner(role)
        elif min_role == "admin":
            allowed = require_admin(role)
        elif min_role == "member":
            allowed = require_member(role)
        else:
            allowed = require_viewer(role)

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )

        return identity

    return _checker
