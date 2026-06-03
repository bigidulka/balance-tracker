import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import Integration, Plan, Subscription, SyncJob


def _make_policy(
    *,
    max_cex_accounts: int,
    max_evm_wallets: int,
    refresh_interval_seconds: int,
    allow_dex: bool = True,
) -> dict[str, Any]:
    total_integrations = max(0, max_cex_accounts) + max(0, max_evm_wallets)
    clamped = max(0, refresh_interval_seconds)
    bg_clamped = max(1, refresh_interval_seconds)
    return {
        "version": 1,
        "features": {"allow_dex": allow_dex},
        "limits": {
            "max_integrations": total_integrations,
            "max_accounts_per_exchange": 0,
            "max_cex_accounts": max(0, max_cex_accounts),
            "max_wallets": max(0, max_evm_wallets),
            "max_evm_wallets": max(0, max_evm_wallets),  # legacy alias
            "min_refresh_interval_seconds": clamped,
        },
        "background": {
            "enabled": True,
            "refresh_interval_seconds": clamped,
        },
        "throttling": {
            "min_refresh_interval_seconds": clamped,
            "background_refresh_interval_seconds": bg_clamped,
        },
    }


class EntitlementsService:
    TARIFFS_PATH = Path(__file__).resolve().parents[2] / "data" / "tariffs.json"
    EVM_CHAIN_WHITELIST = {
        "eth",
        "ethereum",
        "arb",
        "arbitrum",
        "op",
        "optimism",
        "base",
        "matic",
        "bsc",
        "polygon",
        "avax",
        "avalanche",
        "ftm",
        "linea",
        "scroll",
        "zksync",
        "blast",
        "mantle",
        "xdai",
        "gnosis",
        "fantom",
        "berachain",
        "sepolia",
        "holesky",
    }

    DEFAULT_POLICY: dict[str, Any] = _make_policy(
        max_cex_accounts=5,
        max_evm_wallets=1,
        refresh_interval_seconds=600,
        allow_dex=True,
    )

    FREE_POLICY: dict[str, Any] = _make_policy(
        max_cex_accounts=5,
        max_evm_wallets=1,
        refresh_interval_seconds=600,
        allow_dex=True,
    )

    LOW_POLICY: dict[str, Any] = _make_policy(
        max_cex_accounts=14,
        max_evm_wallets=3,
        refresh_interval_seconds=300,
        allow_dex=True,
    )

    MEDIUM_POLICY: dict[str, Any] = _make_policy(
        max_cex_accounts=28,
        max_evm_wallets=7,
        refresh_interval_seconds=120,
        allow_dex=True,
    )

    PRO_POLICY: dict[str, Any] = _make_policy(
        max_cex_accounts=56,
        max_evm_wallets=15,
        refresh_interval_seconds=0,
        allow_dex=True,
    )

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _normalize_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _copy_policy(policy: dict[str, Any]) -> dict[str, Any]:
        copied: dict[str, Any] = {}
        if "version" in policy:
            copied["version"] = policy["version"]
        if isinstance(policy.get("features"), dict):
            copied["features"] = dict(policy["features"])
        if isinstance(policy.get("limits"), dict):
            copied["limits"] = dict(policy["limits"])
        if isinstance(policy.get("state"), dict):
            copied["state"] = dict(policy["state"])
        if isinstance(policy.get("throttling"), dict):
            copied["throttling"] = dict(policy["throttling"])
        if isinstance(policy.get("capabilities"), dict):
            copied["capabilities"] = dict(policy["capabilities"])
        if isinstance(policy.get("background"), dict):
            copied["background"] = dict(policy["background"])
        return copied

    @classmethod
    @lru_cache(maxsize=1)
    def _load_tariff_config(cls) -> dict[str, Any]:
        if cls.TARIFFS_PATH.exists():
            with cls.TARIFFS_PATH.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                return loaded
        return {
            "plans": [
                {
                    "code": "free",
                    "name": "Free",
                    "price_monthly": 0.0,
                    "currency": "USD",
                    "policy": cls._copy_policy(cls.FREE_POLICY),
                },
                {
                    "code": "low",
                    "name": "Low",
                    "price_monthly": 5.0,
                    "currency": "USD",
                    "policy": cls._copy_policy(cls.LOW_POLICY),
                },
                {
                    "code": "medium",
                    "name": "Medium",
                    "price_monthly": 10.0,
                    "currency": "USD",
                    "policy": cls._copy_policy(cls.MEDIUM_POLICY),
                },
                {
                    "code": "pro",
                    "name": "Pro",
                    "price_monthly": 20.0,
                    "currency": "USD",
                    "policy": cls._copy_policy(cls.PRO_POLICY),
                },
            ]
        }

    @classmethod
    def _default_plan_definitions(cls) -> list[dict[str, Any]]:
        config = cls._load_tariff_config()
        plans = config.get("plans") if isinstance(config, dict) else None
        definitions: list[dict[str, Any]] = []
        if not isinstance(plans, list):
            return definitions
        for item in plans:
            if not isinstance(item, dict):
                continue
            policy = item.get("policy")
            if not isinstance(policy, dict):
                continue
            policy_limits = (
                policy.get("limits") if isinstance(policy.get("limits"), dict) else {}
            )
            policy_background = (
                policy.get("background")
                if isinstance(policy.get("background"), dict)
                else {}
            )
            legacy_max_integrations = cls._safe_int(
                policy_limits.get("max_integrations"),
                cls.DEFAULT_POLICY["limits"]["max_integrations"],
            )
            max_cex_accounts = cls._safe_int(
                policy_limits.get("max_cex_accounts"),
                legacy_max_integrations,
            )
            max_evm_wallets = cls._safe_int(
                policy_limits.get("max_wallets")
                or policy_limits.get("max_evm_wallets"),
                0,
            )
            refresh_interval_seconds = cls._safe_int(
                policy_background.get("refresh_interval_seconds")
                if policy_background
                else policy_limits.get("min_refresh_interval_seconds"),
                cls.DEFAULT_POLICY["background"]["refresh_interval_seconds"],
            )
            normalized_policy = cls._resolve_policy(
                Plan(
                    code=str(item.get("code") or "free"),
                    name=str(item.get("name") or "Free"),
                    max_integrations=max_cex_accounts + max_evm_wallets,
                    min_refresh_interval_seconds=refresh_interval_seconds,
                    policy_json=policy,
                    is_active=True,
                )
            )
            definitions.append(
                {
                    "code": str(item.get("code") or "free"),
                    "name": str(item.get("name") or "Free"),
                    "max_integrations": normalized_policy["limits"]["max_integrations"],
                    "min_refresh_interval_seconds": normalized_policy["throttling"][
                        "background_refresh_interval_seconds"
                    ],
                    "policy_json": cls._copy_policy(normalized_policy),
                    "price_monthly": float(item.get("price_monthly") or 0.0),
                    "currency": str(item.get("currency") or "USD"),
                }
            )
        return definitions

    @classmethod
    def _configured_plan_codes(cls) -> set[str]:
        return {definition["code"] for definition in cls._default_plan_definitions()}

    async def ensure_default_plans(self) -> None:
        definitions = self._default_plan_definitions()

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
                or float(plan.price_monthly or 0.0)
                != float(definition["price_monthly"])
                or (plan.currency or "USD") != definition["currency"]
                or not plan.is_active
            ):
                plan.name = definition["name"]
                plan.max_integrations = definition["max_integrations"]
                plan.min_refresh_interval_seconds = definition[
                    "min_refresh_interval_seconds"
                ]
                plan.policy_json = definition["policy_json"]
                plan.price_monthly = float(definition["price_monthly"])
                plan.currency = definition["currency"]
                plan.is_active = True
                changed = True

        if changed:
            await self.session.commit()

    async def list_active_plans(self) -> list[Plan]:
        configured_codes = self._configured_plan_codes()
        if not configured_codes:
            return []
        result = await self.session.execute(
            select(Plan)
            .where(Plan.is_active == True, Plan.code.in_(configured_codes))
            .order_by(Plan.id.asc())
        )
        return list(result.scalars().all())

    async def get_active_subscription(
        self, organization_id: int
    ) -> Subscription | None:
        result = await self.session.execute(
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
        return result.scalar_one_or_none()

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
            select(Plan)
            .where(and_(Plan.code == "free", Plan.is_active == True))
            .limit(1)
        )
        plan = fallback.scalar_one_or_none()
        if plan:
            return plan

        return Plan(
            code="free",
            name="Free",
            max_integrations=self.FREE_POLICY["limits"]["max_integrations"],
            min_refresh_interval_seconds=self.FREE_POLICY["background"][
                "refresh_interval_seconds"
            ],
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

    @staticmethod
    def _safe_bool(value: Any, fallback: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return fallback

    @classmethod
    def _resolve_policy(cls, plan: Plan) -> dict[str, Any]:
        policy: dict[str, Any] = cls._copy_policy(cls.DEFAULT_POLICY)
        cex_limit_explicit = False
        evm_limit_explicit = False
        background_refresh_explicit = False
        throttling_refresh_explicit = False

        policy["limits"]["max_integrations"] = cls._safe_int(
            getattr(plan, "max_integrations", None),
            policy["limits"]["max_integrations"],
        )
        policy["limits"]["min_refresh_interval_seconds"] = cls._safe_int(
            getattr(plan, "min_refresh_interval_seconds", None),
            policy["limits"]["min_refresh_interval_seconds"],
        )
        policy["limits"]["max_cex_accounts"] = cls._safe_int(
            policy["limits"].get("max_cex_accounts"),
            policy["limits"]["max_integrations"],
        )
        policy["limits"]["max_wallets"] = cls._safe_int(
            policy["limits"].get("max_wallets")
            or policy["limits"].get("max_evm_wallets"),
            0,
        )
        policy["limits"]["max_evm_wallets"] = policy["limits"][
            "max_wallets"
        ]  # legacy alias
        policy["limits"]["max_accounts_per_exchange"] = cls._safe_int(
            policy["limits"].get("max_accounts_per_exchange"),
            0,
        )
        policy.setdefault("background", {})
        policy["background"]["enabled"] = bool(
            policy["background"].get("enabled", True)
        )
        policy["background"]["refresh_interval_seconds"] = cls._safe_int(
            policy["background"].get("refresh_interval_seconds"),
            policy["limits"]["min_refresh_interval_seconds"],
        )
        policy.setdefault("throttling", {})
        policy["throttling"]["background_refresh_interval_seconds"] = cls._safe_int(
            policy["throttling"].get("background_refresh_interval_seconds"),
            policy["background"]["refresh_interval_seconds"],
        )
        policy["throttling"]["min_refresh_interval_seconds"] = cls._safe_int(
            policy["throttling"].get("min_refresh_interval_seconds"),
            policy["background"]["refresh_interval_seconds"],
        )

        raw_policy = getattr(plan, "policy_json", None)
        if isinstance(raw_policy, dict):
            policy["version"] = cls._safe_int(
                raw_policy.get("version"), policy["version"]
            )

            raw_limits = raw_policy.get("limits")
            if isinstance(raw_limits, dict):
                if "max_integrations" in raw_limits:
                    policy["limits"]["max_integrations"] = cls._safe_int(
                        raw_limits.get("max_integrations"),
                        policy["limits"]["max_integrations"],
                    )
                if "max_cex_accounts" in raw_limits:
                    policy["limits"]["max_cex_accounts"] = cls._safe_int(
                        raw_limits.get("max_cex_accounts"),
                        policy["limits"]["max_cex_accounts"],
                    )
                    cex_limit_explicit = True
                if "max_wallets" in raw_limits or "max_evm_wallets" in raw_limits:
                    policy["limits"]["max_wallets"] = cls._safe_int(
                        raw_limits.get("max_wallets")
                        or raw_limits.get("max_evm_wallets"),
                        policy["limits"]["max_wallets"],
                    )
                    policy["limits"]["max_evm_wallets"] = policy["limits"][
                        "max_wallets"
                    ]  # legacy alias
                    evm_limit_explicit = True
                if "max_accounts_per_exchange" in raw_limits:
                    policy["limits"]["max_accounts_per_exchange"] = cls._safe_int(
                        raw_limits.get("max_accounts_per_exchange"),
                        policy["limits"]["max_accounts_per_exchange"],
                    )
                if "min_refresh_interval_seconds" in raw_limits:
                    policy["limits"]["min_refresh_interval_seconds"] = cls._safe_int(
                        raw_limits.get("min_refresh_interval_seconds"),
                        policy["limits"]["min_refresh_interval_seconds"],
                    )

            raw_background = raw_policy.get("background")
            if isinstance(raw_background, dict):
                if "enabled" in raw_background:
                    policy["background"]["enabled"] = cls._safe_bool(
                        raw_background.get("enabled"),
                        policy["background"]["enabled"],
                    )
                if "refresh_interval_seconds" in raw_background:
                    policy["background"]["refresh_interval_seconds"] = cls._safe_int(
                        raw_background.get("refresh_interval_seconds"),
                        policy["background"]["refresh_interval_seconds"],
                    )
                    background_refresh_explicit = True

            raw_throttling = raw_policy.get("throttling")
            if isinstance(raw_throttling, dict):
                if "min_refresh_interval_seconds" in raw_throttling:
                    policy["throttling"]["min_refresh_interval_seconds"] = (
                        cls._safe_int(
                            raw_throttling.get("min_refresh_interval_seconds"),
                            policy["throttling"]["min_refresh_interval_seconds"],
                        )
                    )
                    throttling_refresh_explicit = True
                if "background_refresh_interval_seconds" in raw_throttling:
                    policy["throttling"]["background_refresh_interval_seconds"] = (
                        cls._safe_int(
                            raw_throttling.get("background_refresh_interval_seconds"),
                            policy["throttling"]["background_refresh_interval_seconds"],
                        )
                    )

            raw_features = raw_policy.get("features")
            if not isinstance(raw_features, dict):
                raw_features = raw_policy.get("capabilities")
            if isinstance(raw_features, dict):
                if "allow_dex" in raw_features:
                    policy["features"]["allow_dex"] = cls._safe_bool(
                        raw_features.get("allow_dex"),
                        policy["features"]["allow_dex"],
                    )
                if "allow_evm" in raw_features:
                    policy["features"]["allow_dex"] = cls._safe_bool(
                        raw_features.get("allow_evm"),
                        policy["features"]["allow_dex"],
                    )

            for legacy_key in (
                "max_integrations",
                "max_accounts_per_exchange",
                "min_refresh_interval_seconds",
            ):
                if legacy_key in raw_policy:
                    policy["limits"][legacy_key] = cls._safe_int(
                        raw_policy.get(legacy_key),
                        policy["limits"][legacy_key],
                    )
            if "allow_dex" in raw_policy:
                policy["features"]["allow_dex"] = cls._safe_bool(
                    raw_policy.get("allow_dex"),
                    policy["features"]["allow_dex"],
                )

        if not cex_limit_explicit:
            policy["limits"]["max_cex_accounts"] = policy["limits"].get(
                "max_integrations", 0
            )
        if not evm_limit_explicit:
            policy["limits"]["max_wallets"] = 0
            policy["limits"]["max_evm_wallets"] = 0  # legacy alias
        if throttling_refresh_explicit and not background_refresh_explicit:
            policy["background"]["refresh_interval_seconds"] = policy["throttling"][
                "min_refresh_interval_seconds"
            ]
            policy["throttling"]["background_refresh_interval_seconds"] = policy[
                "throttling"
            ]["min_refresh_interval_seconds"]
        if background_refresh_explicit and not throttling_refresh_explicit:
            policy["throttling"]["min_refresh_interval_seconds"] = policy["background"][
                "refresh_interval_seconds"
            ]
        if policy["background"].get("refresh_interval_seconds") is None:
            policy["background"]["refresh_interval_seconds"] = policy["limits"][
                "min_refresh_interval_seconds"
            ]
        if policy["throttling"].get("min_refresh_interval_seconds") is None:
            policy["throttling"]["min_refresh_interval_seconds"] = policy["background"][
                "refresh_interval_seconds"
            ]
        if policy["throttling"].get("background_refresh_interval_seconds") is None:
            policy["throttling"]["background_refresh_interval_seconds"] = policy[
                "background"
            ]["refresh_interval_seconds"]
        policy["limits"]["min_refresh_interval_seconds"] = policy["background"][
            "refresh_interval_seconds"
        ]
        policy["limits"]["max_integrations"] = max(
            policy["limits"].get("max_integrations", 0),
            policy["limits"].get("max_cex_accounts", 0)
            + policy["limits"].get("max_wallets", 0),
        )

        return policy

    @classmethod
    def _normalize_chain(cls, chain: str | None) -> str:
        return (chain or "").strip().lower()

    @classmethod
    def _is_evm_chain(cls, chain: str | None) -> bool:
        return cls._normalize_chain(chain) in cls.EVM_CHAIN_WHITELIST

    async def _get_active_integration_count(
        self,
        organization_id: int,
        *,
        kind: str | None = None,
        exchange_code: str | None = None,
        chain: str | None = None,
        chain_in: set[str] | None = None,
        chain_not_in: set[str] | None = None,
    ) -> int:
        query = select(func.count(Integration.id)).where(
            and_(
                Integration.organization_id == organization_id,
                Integration.is_active == True,
            )
        )
        if kind is not None:
            query = query.where(Integration.kind == kind)
        if exchange_code is not None:
            query = query.where(Integration.exchange_code == exchange_code)
        if chain is not None:
            query = query.where(Integration.chain == chain)
        if chain_in:
            query = query.where(Integration.chain.in_(sorted(chain_in)))
        if chain_not_in:
            query = query.where(~Integration.chain.in_(sorted(chain_not_in)))
        count_result = await self.session.execute(query)
        return int(count_result.scalar_one() or 0)

    async def _get_active_usage(self, organization_id: int) -> dict[str, int]:
        cex_active = await self._get_active_integration_count(
            organization_id, kind="cex"
        )
        evm_active = await self._get_active_integration_count(
            organization_id,
            kind="dex",
            chain_in=self.EVM_CHAIN_WHITELIST,
        )
        non_evm_active = await self._get_active_integration_count(
            organization_id,
            kind="dex",
            chain_not_in=self.EVM_CHAIN_WHITELIST,
        )
        wallets_active = evm_active + non_evm_active
        return {
            "cex": cex_active,
            "evm": evm_active,
            "non_evm_dex": non_evm_active,
            "wallets": wallets_active,
            "integrations": cex_active + wallets_active,
        }

    @staticmethod
    def _build_refresh_state(
        *,
        last_refresh_at: datetime | None,
        min_refresh_interval_seconds: int,
    ) -> dict[str, Any]:
        normalized_last_refresh_at = EntitlementsService._normalize_datetime(
            last_refresh_at
        )
        retry_after_seconds = 0
        can_refresh = True

        if min_refresh_interval_seconds > 0 and normalized_last_refresh_at is not None:
            elapsed_seconds = int(
                (
                    datetime.now(timezone.utc) - normalized_last_refresh_at
                ).total_seconds()
            )
            if elapsed_seconds < min_refresh_interval_seconds:
                can_refresh = False
                retry_after_seconds = max(
                    min_refresh_interval_seconds - elapsed_seconds, 1
                )

        return {
            "last_refresh_at": (
                normalized_last_refresh_at.isoformat()
                if normalized_last_refresh_at is not None
                else None
            ),
            "can_refresh": can_refresh,
            "retry_after_seconds": retry_after_seconds,
            "min_refresh_interval_seconds": min_refresh_interval_seconds,
        }

    async def ensure_can_create_integration(
        self,
        organization_id: int,
        kind: str | None = None,
        exchange_code: str | None = None,
        chain: str | None = None,
    ) -> None:
        normalized_kind = kind.lower() if kind else None
        normalized_exchange_code = exchange_code.lower() if exchange_code else None
        normalized_chain = self._normalize_chain(chain)

        plan = await self._get_active_plan(organization_id)
        policy = self._resolve_policy(plan)

        allow_dex = policy["features"].get("allow_dex", False)
        max_cex_accounts = int(policy["limits"].get("max_cex_accounts") or 0)
        max_wallets = int(
            policy["limits"].get("max_wallets")
            or policy["limits"].get("max_evm_wallets")
            or 0
        )
        max_accounts_per_exchange = int(
            policy["limits"].get("max_accounts_per_exchange") or 0
        )

        if normalized_kind == "dex" and not allow_dex:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "dex_not_allowed",
                    "message": "Current plan does not allow DEX integrations",
                    "policy": {"allow_dex": allow_dex},
                },
            )

        if normalized_kind == "cex":
            current_cex_accounts = await self._get_active_integration_count(
                organization_id,
                kind="cex",
            )
            if max_cex_accounts > 0 and current_cex_accounts >= max_cex_accounts:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "cex_account_limit_reached",
                        "message": f"Plan limit reached: max {max_cex_accounts} CEX accounts",
                        "policy": {"max_cex_accounts": max_cex_accounts},
                    },
                )
            if max_accounts_per_exchange > 0 and normalized_exchange_code:
                current_accounts_on_exchange = await self._get_active_integration_count(
                    organization_id,
                    kind="cex",
                    exchange_code=normalized_exchange_code,
                )
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
            return

        if normalized_kind == "dex":
            current_wallets = await self._get_active_integration_count(
                organization_id,
                kind="dex",
            )
            if max_wallets > 0 and current_wallets >= max_wallets:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "wallet_limit_reached",
                        "message": f"Plan limit reached: max {max_wallets} wallets",
                        "policy": {"max_wallets": max_wallets},
                    },
                )
            return

    async def get_latest_successful_refresh_at(
        self, organization_id: int
    ) -> datetime | None:
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
        min_refresh_interval_seconds = policy["background"]["refresh_interval_seconds"]

        if min_refresh_interval_seconds <= 0:
            return

        if last_refresh_at is None:
            return

        last_refresh_at = self._normalize_datetime(last_refresh_at)

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

        usage = await self._get_active_usage(organization_id)
        cex_limit = int(policy["limits"].get("max_cex_accounts") or 0)
        wallet_limit = int(
            policy["limits"].get("max_wallets")
            or policy["limits"].get("max_evm_wallets")
            or 0
        )
        total_limit = int(
            policy["limits"].get("max_integrations") or (cex_limit + wallet_limit)
        )
        last_refresh_at = await self.get_latest_successful_refresh_at(organization_id)
        refresh_state = self._build_refresh_state(
            last_refresh_at=last_refresh_at,
            min_refresh_interval_seconds=policy["throttling"][
                "min_refresh_interval_seconds"
            ],
        )
        allow_dex = policy["features"].get("allow_dex", False)
        cex_remaining = max(cex_limit - usage["cex"], 0) if cex_limit > 0 else 0
        wallet_remaining = (
            max(wallet_limit - usage["wallets"], 0) if wallet_limit > 0 else 0
        )
        limited_active = usage["integrations"]
        limited_remaining = (
            max(total_limit - limited_active, 0) if total_limit > 0 else 0
        )
        cex_limit_reached = cex_limit > 0 and usage["cex"] >= cex_limit
        wallet_limit_reached = wallet_limit > 0 and usage["wallets"] >= wallet_limit

        resolved_policy = {
            "version": policy["version"],
            "features": {
                "allow_dex": allow_dex,
            },
            "limits": {
                "max_integrations": total_limit,
                "max_accounts_per_exchange": policy["limits"].get(
                    "max_accounts_per_exchange"
                ),
                "max_cex_accounts": cex_limit,
                "max_wallets": wallet_limit,
                "max_evm_wallets": wallet_limit,  # legacy alias
                "min_refresh_interval_seconds": policy["background"][
                    "refresh_interval_seconds"
                ],
            },
            "background": {
                "enabled": bool(policy["background"].get("enabled", True)),
                "refresh_interval_seconds": policy["background"][
                    "refresh_interval_seconds"
                ],
            },
            "throttling": {
                "min_refresh_interval_seconds": policy["throttling"][
                    "min_refresh_interval_seconds"
                ],
                "background_refresh_interval_seconds": policy["throttling"][
                    "background_refresh_interval_seconds"
                ],
                "retry_after_seconds": refresh_state["retry_after_seconds"],
            },
            "state": {
                "refresh": refresh_state,
                "cex": {
                    "active": usage["cex"],
                    "remaining": cex_remaining,
                    "limit_reached": cex_limit_reached,
                },
                "evm": {
                    "active": usage["evm"],
                    "remaining": 0,
                    "limit_reached": False,
                },
                "wallets": {
                    "active": usage["wallets"],
                    "remaining": wallet_remaining,
                    "limit_reached": wallet_limit_reached,
                },
                "non_evm_dex": {
                    "active": usage["non_evm_dex"],
                },
                "integrations": {
                    "active": limited_active,
                    "remaining": limited_remaining,
                    "limit_reached": total_limit > 0 and limited_active >= total_limit,
                },
            },
        }

        return {
            "plan": {
                "code": getattr(plan, "code", "free"),
                "name": getattr(plan, "name", "Free"),
            },
            "policy": resolved_policy,
            "limits": {
                "max_integrations": total_limit,
                "max_accounts_per_exchange": policy["limits"].get(
                    "max_accounts_per_exchange"
                ),
                "max_cex_accounts": cex_limit,
                "max_wallets": wallet_limit,
                "max_evm_wallets": wallet_limit,  # legacy alias
            },
            "throttling": {
                "min_refresh_interval_seconds": policy["throttling"][
                    "min_refresh_interval_seconds"
                ],
                "background_refresh_interval_seconds": policy["throttling"][
                    "background_refresh_interval_seconds"
                ],
                "retry_after_seconds": refresh_state["retry_after_seconds"],
            },
            "background": {
                "enabled": bool(policy["background"].get("enabled", True)),
                "refresh_interval_seconds": policy["background"][
                    "refresh_interval_seconds"
                ],
            },
            "usage": {
                "cex": {
                    "active": usage["cex"],
                    "remaining": cex_remaining,
                    "limit_reached": cex_limit_reached,
                },
                "evm": {
                    "active": usage["evm"],
                    "remaining": 0,
                    "limit_reached": False,
                },
                "wallets": {
                    "active": usage["wallets"],
                    "remaining": wallet_remaining,
                    "limit_reached": wallet_limit_reached,
                },
                "non_evm_dex": {
                    "active": usage["non_evm_dex"],
                },
                "integrations": {
                    "active": limited_active,
                    "remaining": limited_remaining,
                    "limit_reached": total_limit > 0 and limited_active >= total_limit,
                },
            },
            "capabilities": {
                "allow_dex": allow_dex,
                "refresh": refresh_state["can_refresh"],
                "can_refresh": refresh_state["can_refresh"],
                "balances_refresh": refresh_state["can_refresh"],
                "can_add_cex": not cex_limit_reached,
                "can_add_evm": allow_dex and not wallet_limit_reached,
                "can_add_wallet": allow_dex and not wallet_limit_reached,
            },
            "last_refresh_at": refresh_state["last_refresh_at"],
        }
