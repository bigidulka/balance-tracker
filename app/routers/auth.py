from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import IdentityContext, get_identity_context, require_role
from app.repositories.auth import AuthRepository
from app.schemas.auth import (
    IdentityResponse,
    LoginRequest,
    OrganizationInfo,
    RegisterRequest,
    TelegramBootstrapRequest,
    TelegramBootstrapResponse,
    TokenResponse,
    UserProfileResponse,
)
from app.services.auth_service import AuthService, AuthServiceError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserProfileResponse)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(AuthRepository(db))
    try:
        user = await service.register(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
        )
    except AuthServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return UserProfileResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(AuthRepository(db))

    try:
        token = await service.login(
            email=payload.email,
            password=payload.password,
            organization_id=payload.organization_id,
        )
        identity = await service.resolve_identity(token)
    except AuthServiceError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return TokenResponse(
        access_token=token,
        user_id=identity.user.id,
        organization_id=identity.organization.id,
        role=identity.membership.role,
    )


@router.get("/me", response_model=IdentityResponse)
async def me(identity: IdentityContext = Depends(get_identity_context)):
    return IdentityResponse(
        user=UserProfileResponse(
            id=identity.user.id,
            email=identity.user.email,
            full_name=identity.user.full_name,
            telegram_user_id=identity.user.telegram_user_id,
            telegram_username=identity.user.telegram_username,
            telegram_full_name=identity.user.telegram_full_name,
            is_active=identity.user.is_active,
            created_at=identity.user.created_at,
        ),
        organization=OrganizationInfo(
            id=identity.organization.id,
            name=identity.organization.name,
            slug=identity.organization.slug,
            role=identity.membership.role,
        ),
    )


@router.post("/telegram/bootstrap", response_model=TelegramBootstrapResponse)
@router.post("/telegram/resolve", response_model=TelegramBootstrapResponse)
async def telegram_bootstrap(
    payload: TelegramBootstrapRequest,
    db: AsyncSession = Depends(get_db),
    _: IdentityContext = Depends(require_role("owner")),
):
    service = AuthService(AuthRepository(db))
    try:
        result = await service.bootstrap_telegram_identity(
            telegram_user_id=payload.telegram_user_id,
            telegram_username=payload.telegram_username,
            telegram_first_name=payload.telegram_first_name,
            telegram_last_name=payload.telegram_last_name,
            telegram_full_name=payload.telegram_full_name,
        )
    except AuthServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return TelegramBootstrapResponse(
        access_token=result.access_token,
        user_id=result.user.id,
        organization_id=result.organization.id,
        organization_name=result.organization.name,
        organization_slug=result.organization.slug,
        role=result.membership.role,
        is_platform_admin=result.is_platform_admin,
        telegram_user_id=result.telegram_identity.telegram_user_id,
        telegram_username=result.telegram_identity.telegram_username,
        telegram_full_name=result.telegram_identity.telegram_full_name,
    )
