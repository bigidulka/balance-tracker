from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import IdentityContext, get_identity_context
from app.repositories.auth import AuthRepository
from app.schemas.auth import (
    IdentityResponse,
    LoginRequest,
    OrganizationInfo,
    RegisterRequest,
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
    )


@router.get("/me", response_model=IdentityResponse)
async def me(identity: IdentityContext = Depends(get_identity_context)):
    return IdentityResponse(
        user=UserProfileResponse(
            id=identity.user.id,
            email=identity.user.email,
            full_name=identity.user.full_name,
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
