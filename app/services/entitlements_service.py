from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import Integration, Plan, Subscription, SyncJob


class EntitlementsService:
    DEFAULT_POLICY: dict[str, Any] = {
        "limits": {
            "max_integrations": 1000,
            "max_accounts_per_exchange": 1,
        },
        "throttling": {
            "min_refresh_interval_seconds": 3600,
        },
        "capabilities": {
            "allow_dex": 0,
        },
    }

    FREE_POLICY: dict[str, Any] = {
        "limits": {
            "max_integrations": 1000,
            "max_accounts_per_exchange": 1,
        },
        "throttling": {
            "min_refresh_interval_seconds": 3600,
        },
        "capabilities": {
            "allow_dex": 0,
        },
    }

    FULL_POLICY: dict[str, Any] = {
        "limits": {
            "max_integrations": 1000,
            "max_accounts_per_exchange": 0,
        },
        "throttling": {
            "min_refresh_interval_seconds": 300,
        },
        "capabilities": {
            "allow_dex": 1,
        },
    }

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _copy_policy(policy: dict[str, Any]) -> dict[str, dict[str, int]]:
        return {
            "limits": dict(policy.get("limits", {})),
            "throttling": dict(policy.get("throttling", {})),
            "capabilities": dict(policy.get("capabilities", {})),
        }

    async def ensure_default_plans(self) -> None:
        definitions = [
            {
                "code": "free",
                "name": "Free",
                "max_integrations": 1000,
                "min_refresh_interval_seconds": 3600,
                "policy_json": self._copy_policy(self.FREE_POLICY),
                "price_monthly": 0.0,
                "currency": "USD",
            },
            {
                "code": "full",
                "name": "Full",
                "max_integrations": 1000,
                "min_refresh_interval_seconds": 300,
                "policy_json": self._copy_policy(self.FULL_POLICY),
                "price_monthly": 0.0,
                "currency": "USD",
            },
        ]

        changed = False
        for definition in definitions:
            result = await self.session.execute(
                select(Plan).where(Plan.code == definition["code"]).limit(1)
            )
            plan = result.scalar_one_or_none()

            if plan is None:
                self.session.add(Plan(**definition, is_active=True))
                changed = True
                continue

            if (
                plan.name != definition["name"]
                or plan.max_integrations != definition["max_integrations"]
                or plan.min_refresh_interval_seconds
                != definition["min_refresh_interval_seconds"]
                or plan.policy_json != definition["policy_json"]
                or not plan.is_active
            ):
                plan.name = definition["name"]
                plan.max_integrations = definition["max_integrations"]
                plan.min_refresh_interval_seconds = definition[
                    "min_refresh_interval_seconds"
                ]
                plan.policy_json = definition["policy_json"]
                plan.is_active = True
                changed = True

        if changed:
            await self.session.commit()

    async def ensure_default_subscription(self, organization_id: int) -> None:
        existing = await self.session.execute(
            select(Subscription)
            .where(
                and_(
                    Subscription.organization_id == organization_id,
                    Subscription.status == "active",
                )
            )
            .order_by(Subscription.updated_at.desc())
            .limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return

        await self.ensure_default_plans()
        free_plan_result = await self.session.execute(
            select(Plan)
            .where(and_(Plan.code == "free", Plan.is_active == True))
            .limit(1)
        )
        free_plan = free_plan_result.scalar_one_or_none()
        if free_plan is None:
            return

        self.session.add(
            Subscription(
                organization_id=organization_id,
                plan_id=free_plan.id,
                status="active",
            )
        )
        await self.session.commit()

    async def _get_active_plan(self, organization_id: int) -> Plan:
        query = (
            select(Plan)
            .join(Subscription, Subscription.plan_id == Plan.id)
            .where(
                and_(
                    Subscription.organization_id == organization_id,
                    Subscription.status == "active",
                    Plan.is_active == True,
                )
            )
            .order_by(Subscription.updated_at.desc())
            .limit(1)
        )
        result = await self.session.execute(query)
        plan = result.scalar_one_or_none()
        if plan:
            return plan

        fallback = await self.session.execute(
            select(Plan).where(and_(Plan.code == "free", Plan.is_active == True)).limit(1)
        )
        plan = fallback.scalar_one_or_none()
        if plan:
            return plan

        return Plan(
            code="free",
            name="Free",
            max_integrations=1000,
            min_refresh_interval_seconds=3600,
            policy_json=self._copy_policy(self.FREE_POLICY),
            is_active=True,
        )

    @staticmethod
    def _safe_int(value: Any, fallback: int) -> int:
        try:
            parsed = int(value)
            if parsed >= 0:
                return parsed
        except (TypeError, ValueError):
            pass
        return fallback

    @classmethod
    def _resolve_policy(cls, plan: Plan) -> dict[str, dict[str, int]]:
        policy: dict[str, dict[str, int]] = {
            "limits": dict(cls.DEFAULT_POLICY["limits"]),
            "throttling": dict(cls.DEFAULT_POLICY["throttling"]),
            "capabilities": dict(cls.DEFAULT_POLICY["capabilities"]),
        }

        policy["limits"]["max_integrations"] = cls._safe_int(
            getattr(plan, "max_integrations", None),
            policy["limits"]["max_integrations"],
        )
        policy["throttling"]["min_refresh_interval_seconds"] = cls._safe_int(
            getattr(plan, "min_refresh_interval_seconds", None),
            policy["throttling"]["min_refresh_interval_seconds"],
        )

        raw_policy = getattr(plan, "policy_json", None)
        if isinstance(raw_policy, dict):
            raw_limits = raw_policy.get("limits")
            if isinstance(raw_limits, dict):
                if "max_integrations" in raw_limits:
                    policy["limits"]["max_integrations"] = cls._safe_int(
                        raw_limits.get("max_integrations"),
                        policy["limits"]["max_integrations"],
                    )
                if "max_accounts_per_exchange" in raw_limits:
                    policy["limits"]["max_accounts_per_exchange"] = cls._safe_int(
                        raw_limits.get("max_accounts_per_exchange"),
                        policy["limits"]["max_accounts_per_exchange"],
                    )

            raw_throttling = raw_policy.get("throttling")
            if (
                isinstance(raw_throttling, dict)
                and "min_refresh_interval_seconds" in raw_throttling
            ):
                policy["throttling"]["min_refresh_interval_seconds"] = cls._safe_int(
                    raw_throttling.get("min_refresh_interval_seconds"),
                    policy["throttling"]["min_refresh_interval_seconds"],
                )

            raw_capabilities = raw_policy.get("capabilities")
            if isinstance(raw_capabilities, dict) and "allow_dex" in raw_capabilities:
                policy["capabilities"]["allow_dex"] = cls._safe_int(
                    raw_capabilities.get("allow_dex"),
                    policy["capabilities"]["allow_dex"],
                )

        return policy

    async def ensure_can_create_integration(
        self,
        organization_id: int,
        kind: str | None = None,
        exchange_code: str | None = None,
    ) -> None:
        normalized_kind = kind.lower() if kind else None
        normalized_exchange_code = exchange_code.lower() if exchange_code else None

        plan = await self._get_active_plan(organization_id)
        policy = self._resolve_policy(plan)

        max_integrations = policy["limits"]["max_integrations"]
        count_query = select(func.count(Integration.id)).where(
            and_(Integration.organization_id == organization_id, Integration.is_active == True)
        )
        count_result = await self.session.execute(count_query)
        current_integrations = count_result.scalar_one()

        if current_integrations >= max_integrations:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "plan_limit_reached",
                    "message": f"Plan limit reached: max {max_integrations} integrations",
                    "policy": {"max_integrations": max_integrations},
                },
            )

        allow_dex = policy["capabilities"].get("allow_dex", 0)
        if normalized_kind == "dex" and allow_dex == 0:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "dex_not_allowed",
                    "message": "Current plan does not allow DEX integrations",
                    "policy": {"allow_dex": allow_dex},
                },
            )

        max_accounts_per_exchange = policy["limits"].get("max_accounts_per_exchange")
        if (
            normalized_kind == "cex"
            and normalized_exchange_code
            and isinstance(max_accounts_per_exchange, int)
            and max_accounts_per_exchange > 0
        ):
            per_exchange_query = select(func.count(Integration.id)).where(
                and_(
                    Integration.organization_id == organization_id,
                    Integration.is_active == True,
                    Integration.kind == "cex",
                    Integration.exchange_code == normalized_exchange_code,
                )
            )
            per_exchange_result = await self.session.execute(per_exchange_query)
            current_accounts_on_exchange = per_exchange_result.scalar_one()

            if current_accounts_on_exchange >= max_accounts_per_exchange:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "exchange_account_limit_reached",
                        "message": (
                            f"Plan limit reached for {normalized_exchange_code}: "
                            f"max {max_accounts_per_exchange} account(s)"
                        ),
                        "policy": {
                            "exchange_code": normalized_exchange_code,
                            "max_accounts_per_exchange": max_accounts_per_exchange,
                        },
                    },
                )

    async def get_latest_successful_refresh_at(self, organization_id: int) -> datetime | None:
        result = await self.session.execute(
            select(SyncJob.finished_at)
            .where(
                SyncJob.organization_id == organization_id,
                SyncJob.job_type == "refresh",
                SyncJob.status.in_(["success", "completed", "partial"]),
                SyncJob.finished_at.is_not(None),
            )
            .order_by(SyncJob.finished_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def ensure_refresh_interval(
        self, organization_id: int, last_refresh_at: datetime | None
    ) -> None:
        plan = await self._get_active_plan(organization_id)
        policy = self._resolve_policy(plan)
        min_refresh_interval_seconds = policy["throttling"]["min_refresh_interval_seconds"]

        if last_refresh_at is None:
            return

        if last_refresh_at.tzinfo is None:
            last_refresh_at = last_refresh_at.replace(tzinfo=timezone.utc)
        else:
            last_refresh_at = last_refresh_at.astimezone(timezone.utc)

        elapsed = datetime.now(timezone.utc) - last_refresh_at
        elapsed_seconds = int(elapsed.total_seconds())
        if elapsed_seconds < min_refresh_interval_seconds:
            retry_after_seconds = max(min_refresh_interval_seconds - elapsed_seconds, 1)
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "refresh_rate_limited",
                    "message": (
                        "Refresh rate limit exceeded for current plan. "
                        f"Minimum interval is {min_refresh_interval_seconds} seconds"
                    ),
                    "min_refresh_interval_seconds": min_refresh_interval_seconds,
                    "retry_after_seconds": retry_after_seconds,
                },
                headers={"Retry-After": str(retry_after_seconds)},
            )

    async def ensure_refresh_interval_for_organization(
        self, organization_id: int
    ) -> datetime | None:
        last_refresh_at = await self.get_latest_successful_refresh_at(organization_id)
        await self.ensure_refresh_interval(organization_id, last_refresh_at)
        return last_refresh_at

    async def get_effective_entitlements(self, organization_id: int) -> dict[str, Any]:
        plan = await self._get_active_plan(organization_id)
        policy = self._resolve_policy(plan)

        min_refresh_interval_seconds = policy["throttling"]["min_refresh_interval_seconds"]
        last_refresh_at = await self.get_latest_successful_refresh_at(organization_id)

        retry_after_seconds = 0
        can_refresh = True
        if last_refresh_at is not None:
            elapsed_seconds = int((datetime.now(timezone.utc) - last_refresh_at).total_seconds())
            if elapsed_seconds < min_refresh_interval_seconds:
                can_refresh = False
                retry_after_seconds = max(min_refresh_interval_seconds - elapsed_seconds, 1)

        allow_dex = policy["capabilities"].get("allow_dex", 0) != 0

        return {
            "plan": {
                "code": getattr(plan, "code", "free"),
                "name": getattr(plan, "name", "Free"),
            },
            "limits": {
                "max_integrations": policy["limits"].get("max_integrations"),
                "max_accounts_per_exchange": policy["limits"].get(
                    "max_accounts_per_exchange"
                ),
            },
            "throttling": {
                "min_refresh_interval_seconds": min_refresh_interval_seconds,
                "retry_after_seconds": retry_after_seconds,
            },
            "capabilities": {
                "allow_dex": allow_dex,
                "refresh": can_refresh,
                "can_refresh": can_refresh,
                "balances_refresh": can_refresh,
            },
            "last_refresh_at": (
                last_refresh_at.astimezone(timezone.utc).isoformat()
                if last_refresh_at is not None
                else None
            ),
        }
