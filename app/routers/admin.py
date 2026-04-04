from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_platform_admin
from app.repositories.admin import AdminRepository
from app.schemas.admin import AdminUserDetailResponse, AdminUsersPageResponse
from app.schemas.billing import RedeemPromoResponse
from app.services.ledger_service import LedgerService
from app.services.promo_service import PromoService

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/users", response_model=AdminUsersPageResponse)
async def list_users(
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_platform_admin),
):
    items, total = await AdminRepository(db).list_user_memberships(limit=limit, offset=offset)
    return AdminUsersPageResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/users/{user_id}", response_model=AdminUserDetailResponse)
async def get_user_detail(
    user_id: int,
    organization_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_platform_admin),
):
    payload = await AdminRepository(db).get_user_membership_detail(
        user_id=user_id,
        organization_id=organization_id,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="User membership not found")
    return AdminUserDetailResponse(**payload)


@router.post("/promos")
async def create_promo_code(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_platform_admin),
):
    promo = await PromoService(db).create_promo_code(
        code=str(payload.get("code") or ""),
        reward_type=str(payload.get("reward_type") or "balance_credit"),
        reward_value=float(payload.get("reward_value") or 0.0),
        reward_currency=str(payload.get("reward_currency") or "USD"),
        plan_code=str(payload.get("plan_code") or "") or None,
        duration_days=int(payload["duration_days"]) if payload.get("duration_days") is not None else None,
        max_redemptions=int(payload["max_redemptions"]) if payload.get("max_redemptions") is not None else None,
        per_org_limit=int(payload.get("per_org_limit") or 1),
    )
    return {
        "id": promo.id,
        "code": promo.code,
        "reward_type": promo.reward_type,
        "reward_value": float(promo.reward_value or 0.0),
        "reward_currency": promo.reward_currency,
        "plan_code": promo.plan_code,
        "duration_days": promo.duration_days,
        "max_redemptions": promo.max_redemptions,
        "is_active": promo.is_active,
    }


@router.get("/promos")
async def list_promo_codes(
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_platform_admin),
):
    items = await PromoService(db).list_promo_codes(limit=50)
    return {
        "items": [
            {
                "id": item.id,
                "code": item.code,
                "reward_type": item.reward_type,
                "reward_value": float(item.reward_value or 0.0),
                "reward_currency": item.reward_currency,
                "plan_code": item.plan_code,
                "duration_days": item.duration_days,
                "max_redemptions": item.max_redemptions,
                "redeemed_count": item.redeemed_count,
                "is_active": item.is_active,
            }
            for item in items
        ]
    }


@router.post("/balance/adjust", response_model=RedeemPromoResponse)
async def adjust_balance(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_platform_admin),
):
    organization_id = int(payload.get("organization_id") or 0)
    if organization_id <= 0:
        raise HTTPException(status_code=400, detail="organization_id is required")
    amount = float(payload.get("amount") or 0.0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    note = str(payload.get("note") or "Admin adjustment")
    await LedgerService(db).add_entry(
        organization_id=organization_id,
        created_by_user_id=None,
        entry_type="credit",
        amount=amount,
        currency="USD",
        source_type="admin_adjustment",
        source_id=None,
        note=note,
        metadata={"source": "admin"},
    )
    await db.commit()
    return RedeemPromoResponse(
        status="applied",
        reward_type="balance_credit",
        reward_value=amount,
        reward_currency="USD",
        plan_code=None,
    )
