import asyncio
import hashlib
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
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


class IdentityContextCache:
    def __init__(self, *, max_entries: int) -> None:
        self.max_entries = max(1, int(max_entries))
        self._values: OrderedDict[str, tuple[float, IdentityContext]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def get_or_set(
        self,
        key: str,
        *,
        ttl_seconds: float,
        loader: Callable[[], Awaitable[IdentityContext]],
    ) -> IdentityContext:
        ttl = max(0.0, float(ttl_seconds))
        if ttl <= 0:
            return await loader()

        cached = await self._get(key, ttl_seconds=ttl)
        if cached is not None:
            return cached

        lock = await self._lock_for_key(key)
        async with lock:
            cached = await self._get(key, ttl_seconds=ttl)
            if cached is not None:
                return cached
            identity = await loader()
            await self._put(key, identity)
            return identity

    async def clear(self) -> None:
        async with self._guard:
            self._values.clear()
            self._locks.clear()

    async def _get(self, key: str, *, ttl_seconds: float) -> IdentityContext | None:
        now = time.monotonic()
        async with self._guard:
            item = self._values.get(key)
            if item is None:
                return None
            cached_at, identity = item
            if now - cached_at >= ttl_seconds:
                self._values.pop(key, None)
                return None
            self._values.move_to_end(key)
            return identity

    async def _put(self, key: str, identity: IdentityContext) -> None:
        async with self._guard:
            self._values[key] = (time.monotonic(), identity)
            self._values.move_to_end(key)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)

    async def _lock_for_key(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock


identity_context_cache = IdentityContextCache(max_entries=settings.auth_context_cache_max_entries)


def _identity_cache_key(token: str, organization_override: int | None) -> str:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{token_hash}:{organization_override or 0}"


async def clear_identity_context_cache() -> None:
    await identity_context_cache.clear()


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
    organization_override = x_organization_id if x_organization_id is not None else organization_id

    async def _load_identity() -> IdentityContext:
        repo = AuthRepository(db)
        service = AuthService(repo)

        try:
            identity = await service.resolve_identity(
                token,
                organization_override=organization_override,
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

    return await identity_context_cache.get_or_set(
        _identity_cache_key(token, organization_override),
        ttl_seconds=settings.auth_context_cache_ttl_seconds,
        loader=_load_identity,
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


async def require_platform_admin(
    identity: IdentityContext = Depends(get_identity_context),
) -> IdentityContext:
    telegram_user_id = getattr(identity.user, "telegram_user_id", None)
    if telegram_user_id != settings.telegram_admin_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access required",
        )
    return identity
