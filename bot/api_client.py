import aiohttp
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from bot.config import settings
from bot.services.runtime import get_current_backend_auth_session


logger = logging.getLogger(__name__)


_MOCK_BALANCES: Dict[str, Any] = {
    "services": [],
    "total_usd": 0.0,
}

_MOCK_DASHBOARD_SUMMARY: Dict[str, Any] = {
    "total_usd": 0.0,
    "exchanges_count": 0,
    "spot_total": 0.0,
    "futures_total": 0.0,
    "dex_total": 0.0,
    "plan": {"name": "UI Preview", "code": "ui_preview"},
    "capabilities": {
        "can_refresh": True,
        "refresh": True,
        "allow_dex": True,
        "dex": True,
    },
    "throttling": {"retry_after_seconds": 0},
    "integrations": {"total": 0, "active": 0},
    "transactions_24h": {"total": 0, "pending": 0},
    "freshness": "mock_preview_mode",
    "latest_updated_at": None,
    "pnl_today": None,
    "pnl_today_pct": None,
    "pnl_24h": None,
    "pnl_24h_pct": None,
    "pnl_7d": None,
    "pnl_7d_pct": None,
    "pnl_30d": None,
    "pnl_30d_pct": None,
}


_MOCK_CAPABILITIES: Dict[str, Any] = {
    "plan": {"name": "UI Preview", "code": "ui_preview"},
    "capabilities": {
        "can_refresh": True,
        "refresh": True,
        "allow_dex": True,
        "dex": True,
    },
    "throttling": {"retry_after_seconds": 0},
}

_MOCK_BILLING_CURRENT: Dict[str, Any] = {
    "plan": {"name": "UI Preview", "code": "ui_preview"},
    "policy": {
        "version": 1,
        "features": {"allow_dex": True},
        "limits": {
            "max_cex_accounts": 5,
            "max_wallets": 1,
            "max_evm_wallets": 1,
            "min_refresh_interval_seconds": 600,
        },
        "background": {"enabled": True, "refresh_interval_seconds": 600},
    },
    "limits": {
        "max_cex_accounts": 5,
        "max_wallets": 1,
        "max_evm_wallets": 1,
    },
    "background": {"enabled": True, "refresh_interval_seconds": 600},
    "usage": {
        "cex": {"active": 0, "remaining": 5, "limit_reached": False},
        "evm": {"active": 0, "remaining": 0, "limit_reached": False},
        "wallets": {"active": 0, "remaining": 1, "limit_reached": False},
        "non_evm_dex": {"active": 0},
        "integrations": {"active": 0, "remaining": 6, "limit_reached": False},
    },
    "throttling": {"min_refresh_interval_seconds": 600, "retry_after_seconds": 0},
    "capabilities": {
        "allow_dex": True,
        "refresh": True,
        "can_refresh": True,
        "can_add_wallet": True,
    },
    "wallet": {"currency": "USD", "available": 0.0, "entries": []},
    "last_refresh_at": None,
}

_MOCK_BILLING_PLANS: Dict[str, Any] = {
    "plans": [
        {
            "code": "free",
            "name": "Free",
            "price_monthly": 0,
            "currency": "USD",
            "policy": {
                "limits": {
                    "max_cex_accounts": 5,
                    "max_wallets": 1,
                    "max_evm_wallets": 1,
                    "min_refresh_interval_seconds": 600,
                },
                "background": {"refresh_interval_seconds": 600},
            },
        },
        {
            "code": "low",
            "name": "Low",
            "price_monthly": 5,
            "currency": "USD",
            "policy": {
                "limits": {
                    "max_cex_accounts": 14,
                    "max_wallets": 3,
                    "max_evm_wallets": 3,
                    "min_refresh_interval_seconds": 300,
                },
                "background": {"refresh_interval_seconds": 300},
            },
        },
        {
            "code": "medium",
            "name": "Medium",
            "price_monthly": 10,
            "currency": "USD",
            "policy": {
                "limits": {
                    "max_cex_accounts": 28,
                    "max_wallets": 7,
                    "max_evm_wallets": 7,
                    "min_refresh_interval_seconds": 120,
                },
                "background": {"refresh_interval_seconds": 120},
            },
        },
        {
            "code": "pro",
            "name": "Pro",
            "price_monthly": 20,
            "currency": "USD",
            "policy": {
                "limits": {
                    "max_cex_accounts": 56,
                    "max_wallets": 15,
                    "max_evm_wallets": 15,
                    "min_refresh_interval_seconds": 0,
                },
                "background": {"refresh_interval_seconds": 0},
            },
        },
    ]
}

_MOCK_HEALTH: Dict[str, Any] = {
    "status": "ok",
    "mode": "no_backend_ui",
}

_MOCK_REFRESH: Dict[str, Any] = {
    "status": "accepted",
    "mode": "no_backend_ui",
}

_MOCK_TRANSACTIONS: Dict[str, Any] = {
    "transactions": [],
    "total": 0,
    "limit": 100,
    "offset": 0,
    "refreshed_at": None,
}

_MOCK_INTEGRATIONS: list[Dict[str, Any]] = []
_MOCK_BILLING_INVOICES: Dict[str, Any] = {"items": []}
_MOCK_ADMIN_USERS: Dict[str, Any] = {"items": [], "total": 0, "limit": 10, "offset": 0}
_MOCK_ADMIN_USER_DETAIL: Dict[str, Any] = {
    "membership_id": 1,
    "organization_id": 1,
    "organization_name": "UI Preview Org",
    "user_id": 1,
    "email": "preview@example.com",
    "full_name": "UI Preview",
    "role": "owner",
    "is_active": True,
    "balance_usd": 0.0,
    "integrations_total": 0,
    "active_integrations": 0,
    "plan_code": "free",
    "plan_name": "Free",
    "created_at": None,
    "integrations": [],
}


class APIClient:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def no_backend_ui_mode(self) -> bool:
        return settings.no_backend_ui_mode

    def current_identity_cache_key(self) -> str:
        session = get_current_backend_auth_session()
        if session:
            organization_id = str(session.get("organization_id") or "unknown")
            access_token = str(session.get("access_token") or "")
            return f"{organization_id}:{access_token[-12:] if access_token else 'anon'}"
        return "global"

    def _build_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        session = get_current_backend_auth_session()
        if session and session.get("access_token"):
            headers["Authorization"] = f"Bearer {session['access_token']}"
            organization_id = str(session.get("organization_id") or "").strip()
            if organization_id:
                headers["X-Organization-Id"] = organization_id
                headers["X-Org-Id"] = organization_id
        return headers

    def _build_service_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if settings.api_token:
            headers["Authorization"] = f"Bearer {settings.api_token}"
        if settings.api_org_id:
            headers["X-Organization-Id"] = settings.api_org_id
            headers["X-Org-Id"] = settings.api_org_id
        return headers

    def _add_org_param(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        merged = dict(params or {})
        session = get_current_backend_auth_session()
        if session and "organization_id" not in merged:
            organization_id = session.get("organization_id")
            if organization_id is not None:
                merged["organization_id"] = organization_id
        return merged

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=20,           # more concurrent connections
                keepalive_timeout=30, # reuse TCP connections
                enable_cleanup_closed=True,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=10, connect=3),  # Fast timeout
            )
        return self._session

    async def get_balances(self, cached: bool = True) -> Dict[str, Any]:
        """Get balances from API

        Args:
            cached: If True, get from DB cache (fast). If False, may fetch live.
        """
        if self.no_backend_ui_mode:
            payload = deepcopy(_MOCK_BALANCES)
            payload["generated_at"] = datetime.now(timezone.utc).isoformat()
            return payload

        session = await self._get_session()
        endpoint = "/api/v1/balances/cached" if cached else "/api/v1/balances"
        async with session.get(
            f"{settings.api_url}{endpoint}",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_dashboard_summary(self, *, include_metrics: bool = False) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            payload = deepcopy(_MOCK_DASHBOARD_SUMMARY)
            payload["freshness"] = (
                f"mock_preview_mode_{datetime.now(timezone.utc).strftime('%H:%M:%S')}"
            )
            payload["latest_updated_at"] = datetime.now(timezone.utc).isoformat()
            return payload

        session = await self._get_session()
        params = self._add_org_param()
        if include_metrics:
            params["include_metrics"] = "true"
        async with session.get(
            f"{settings.api_url}/api/v1/dashboard/summary",
            headers=self._build_headers(),
            params=params,
            timeout=aiohttp.ClientTimeout(total=20, connect=5),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


    async def get_health(self) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return deepcopy(_MOCK_HEALTH)

        session = await self._get_session()
        async with session.get(
            f"{settings.api_url}/api/v1/health",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def refresh(self) -> Dict[str, Any]:
        """Trigger full refresh from all exchanges (slow)"""
        if self.no_backend_ui_mode:
            return deepcopy(_MOCK_REFRESH)

        session = await self._get_session()
        # Longer timeout for full refresh
        timeout = aiohttp.ClientTimeout(total=120)
        async with session.post(
            f"{settings.api_url}/api/v1/refresh",
            headers=self._build_headers(),
            params=self._add_org_param(),
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_transactions(
        self,
        service: Optional[str] = None,
        integration_id: Optional[int] = None,
        tx_type: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get transactions history"""
        if self.no_backend_ui_mode:
            payload = deepcopy(_MOCK_TRANSACTIONS)
            payload["transactions"] = []
            payload["limit"] = limit
            payload["offset"] = offset
            payload["refreshed_at"] = datetime.now(timezone.utc).isoformat()
            return payload

        session = await self._get_session()
        params = {"limit": limit, "offset": offset}
        if service:
            params["service"] = service
        if integration_id is not None:
            params["integration_id"] = integration_id
        if tx_type:
            params["tx_type"] = tx_type
        if status:
            params["status"] = status
        if start_date:
            params["start_date"] = start_date.isoformat()
        if end_date:
            params["end_date"] = end_date.isoformat()

        async with session.get(
            f"{settings.api_url}/api/v1/transactions",
            headers=self._build_headers(),
            params=self._add_org_param(params),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_deposits(
        self, service: Optional[str] = None, limit: int = 50
    ) -> Dict[str, Any]:
        """Get deposits history"""
        return await self.get_transactions(
            service=service, tx_type="deposit", limit=limit
        )

    async def get_withdrawals(
        self, service: Optional[str] = None, limit: int = 50
    ) -> Dict[str, Any]:
        """Get withdrawals history"""
        return await self.get_transactions(
            service=service, tx_type="withdrawal", limit=limit
        )

    async def refresh_transactions(self, since_hours: int = 168) -> Dict[str, Any]:
        """Refresh transactions from all exchanges"""
        if self.no_backend_ui_mode:
            payload = deepcopy(_MOCK_REFRESH)
            payload["since_hours"] = since_hours
            return payload

        session = await self._get_session()
        timeout = aiohttp.ClientTimeout(total=120)
        async with session.post(
            f"{settings.api_url}/api/v1/transactions/refresh",
            headers=self._build_headers(),
            params=self._add_org_param({"since_hours": since_hours}),
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_notification_settings(self) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {
                "enabled": True,
                "system_enabled": True,
                "transaction_enabled": False,
                "balance_enabled": False,
                "muted_services": [],
                "channels": ["telegram"],
            }

        session = await self._get_session()
        async with session.get(
            f"{settings.api_url}/api/v1/notifications/settings",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def update_notification_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            current = await self.get_notification_settings()
            current.update(payload)
            return current

        session = await self._get_session()
        async with session.patch(
            f"{settings.api_url}/api/v1/notifications/settings",
            headers=self._build_headers(),
            params=self._add_org_param(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_notification_events(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {"events": [], "total_count": 0}

        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        session = await self._get_session()
        async with session.get(
            f"{settings.api_url}/api/v1/notifications/events",
            headers=self._build_headers(),
            params=self._add_org_param(params),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def generate_notifications(
        self,
        kind: str = "all",
        *,
        poll_transactions: bool = False,
    ) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {"status": "ok", "system_events": 0, "transaction_events": 0}

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/notifications/generate",
            headers=self._build_headers(),
            params=self._add_org_param(
                {"kind": kind, "poll_transactions": str(bool(poll_transactions)).lower()}
            ),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def mark_notification_event_sent(self, event_id: int) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {"status": "ok"}

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/notifications/events/{event_id}/sent",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def mark_notification_event_failed(self, event_id: int, error_message: str) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {"status": "ok"}

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/notifications/events/{event_id}/failed",
            headers=self._build_headers(),
            params=self._add_org_param(),
            json={"error_message": error_message},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_capabilities(self) -> Dict[str, Any]:
        """Get capabilities/entitlements payload if API provides it."""
        if self.no_backend_ui_mode:
            return deepcopy(_MOCK_CAPABILITIES)

        session = await self._get_session()
        last_error: Optional[Exception] = None

        for endpoint in (
            "/api/v1/entitlements",
            "/api/v1/capabilities",
            "/api/v1/plans/me",
        ):
            try:
                async with session.get(
                    f"{settings.api_url}{endpoint}",
                    headers=self._build_headers(),
                    params=self._add_org_param(),
                ) as resp:
                    if resp.status == 404:
                        continue
                    resp.raise_for_status()
                    payload = await resp.json()
                    if isinstance(payload, dict):
                        return payload
                    return {"raw": payload}
            except Exception as exc:
                last_error = exc
                logger.warning("Capabilities request failed for %s: %s", endpoint, exc)

        if last_error:
            logger.warning("Falling back to empty capabilities payload: %s", last_error)
        return {}

    async def get_billing_current(self) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            payload = deepcopy(_MOCK_BILLING_CURRENT)
            payload["last_refresh_at"] = datetime.now(timezone.utc).isoformat()
            return payload

        session = await self._get_session()
        async with session.get(
            f"{settings.api_url}/api/v1/billing/current",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def bootstrap_telegram_identity(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None = None,
        telegram_full_name: str | None = None,
    ) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {
                "access_token": f"preview-token:{telegram_user_id}",
                "token_type": "bearer",
                "user_id": telegram_user_id,
                "organization_id": 1,
                "organization_name": "UI Preview Org",
                "role": "owner",
                "is_platform_admin": False,
            }
        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/auth/telegram/resolve",
            headers=self._build_service_headers(),
            json={
                "telegram_user_id": telegram_user_id,
                "telegram_username": telegram_username,
                "telegram_full_name": telegram_full_name,
            },
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(str(data))
            return data if isinstance(data, dict) else {}

    async def get_billing_plans(self) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return deepcopy(_MOCK_BILLING_PLANS)

        session = await self._get_session()
        async with session.get(
            f"{settings.api_url}/api/v1/billing/plans",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_billing_balance(self) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return deepcopy(_MOCK_BILLING_CURRENT["wallet"])

        session = await self._get_session()
        async with session.get(
            f"{settings.api_url}/api/v1/billing/balance",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def switch_billing_plan(self, plan_code: str) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            _MOCK_BILLING_CURRENT["plan"] = {
                "name": plan_code.title(),
                "code": plan_code,
            }
            return {"status": "ok", "changed": True, "plan_code": plan_code}

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/billing/switch",
            headers=self._build_headers(),
            params=self._add_org_param(),
            json={"plan_code": plan_code},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def list_payment_invoices(self) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return deepcopy(_MOCK_BILLING_INVOICES)

        session = await self._get_session()
        async with session.get(
            f"{settings.api_url}/api/v1/billing/invoices",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def create_payment_invoice(
        self,
        amount_usd: float | None = None,
        *,
        invoice_type: str = "balance_topup",
        plan_code: str | None = None,
    ) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            now = datetime.now(timezone.utc).isoformat()
            if invoice_type == "plan_purchase" and not plan_code:
                raise ValueError("plan_code is required for plan purchase")
            resolved_amount = round(float(amount_usd or 0.0), 2)
            if invoice_type == "plan_purchase" and resolved_amount <= 0:
                return {
                    "id": 0,
                    "status": "switched",
                    "invoice_type": "plan_purchase",
                    "amount": 0.0,
                    "currency": "USD",
                    "plan_code": plan_code,
                    "description": f"Preview switch to {plan_code}",
                    "created_at": now,
                }
            return {
                "id": 1,
                "status": "pending",
                "invoice_type": invoice_type,
                "amount": resolved_amount,
                "currency": "USD",
                "asset": "USDT",
                "plan_code": plan_code,
                "pay_url": "https://t.me/CryptoBot?start=preview_invoice",
                "bot_invoice_url": "https://t.me/CryptoBot?start=preview_invoice",
                "external_invoice_id": "preview-1",
                "description": "Preview top-up"
                if invoice_type == "balance_topup"
                else f"Preview plan {plan_code}",
                "paid_amount": None,
                "paid_asset": None,
                "paid_usd_amount": None,
                "expires_at": now,
                "paid_at": None,
                "created_at": now,
            }

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/billing/invoices",
            headers=self._build_headers(),
            params=self._add_org_param(),
            json={
                "amount_usd": amount_usd,
                "invoice_type": invoice_type,
                "plan_code": plan_code,
            },
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def refresh_payment_invoice(self, invoice_id: int) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            payload = await self.create_payment_invoice(25.0)
            payload["id"] = invoice_id
            return payload

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/billing/invoices/{invoice_id}/refresh",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def redeem_promo_code(self, code: str) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {
                "status": "applied",
                "reward_type": "balance_credit",
                "reward_value": 10.0,
                "reward_currency": "USD",
                "plan_code": None,
            }

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/billing/promo/redeem",
            headers=self._build_headers(),
            params=self._add_org_param(),
            json={"code": code},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def admin_list_users(self, *, limit: int, offset: int) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            payload = deepcopy(_MOCK_ADMIN_USERS)
            payload["limit"] = limit
            payload["offset"] = offset
            return payload

        session = await self._get_session()
        async with session.get(
            f"{settings.api_url}/api/v1/admin/users",
            headers=self._build_headers(),
            params=self._add_org_param({"limit": limit, "offset": offset}),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def admin_get_user_detail(
        self, *, user_id: int, organization_id: int
    ) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            payload = deepcopy(_MOCK_ADMIN_USER_DETAIL)
            payload["user_id"] = user_id
            payload["organization_id"] = organization_id
            return payload

        session = await self._get_session()
        async with session.get(
            f"{settings.api_url}/api/v1/admin/users/{user_id}",
            headers=self._build_headers(),
            params=self._add_org_param({"organization_id": organization_id}),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_integrations(self, include_inactive: bool = True) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return deepcopy(_MOCK_INTEGRATIONS)

        session = await self._get_session()
        params = {"include_inactive": str(include_inactive).lower()}
        async with session.get(
            f"{settings.api_url}/api/v1/integrations",
            headers=self._build_headers(),
            params=self._add_org_param(params),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def deactivate_integration(self, integration_id: int) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {
                "id": integration_id,
                "status": "deactivated",
                "mode": "no_backend_ui",
            }

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/integrations/{integration_id}/deactivate",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def verify_integration_credentials(
        self, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {"ok": True, "error": None}

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/integrations/verify",
            headers=self._build_headers(),
            params=self._add_org_param(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def create_integration(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            mock = dict(payload)
            mock["id"] = 1
            mock["is_active"] = True
            now = datetime.now(timezone.utc).isoformat()
            mock["created_at"] = now
            mock["updated_at"] = now
            mock["mode"] = "no_backend_ui"
            return mock

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/integrations",
            headers=self._build_headers(),
            params=self._add_org_param(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def activate_integration(self, integration_id: int) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {
                "id": integration_id,
                "status": "activated",
                "mode": "no_backend_ui",
            }

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/integrations/{integration_id}/activate",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def refresh_integration(self, integration_id: int) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {"id": integration_id, "job_id": 0, "mode": "no_backend_ui"}

        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/integrations/{integration_id}/refresh",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def delete_integration(self, integration_id: int) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {"id": integration_id, "status": "deleted", "mode": "no_backend_ui"}

        session = await self._get_session()
        async with session.delete(
            f"{settings.api_url}/api/v1/integrations/{integration_id}",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            if resp.status == 204:
                return {"id": integration_id, "status": "deleted"}
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            return (
                data
                if isinstance(data, dict)
                else {"id": integration_id, "status": "deleted"}
            )

    async def update_integration(
        self, integration_id: int, name: str
    ) -> Dict[str, Any]:
        if self.no_backend_ui_mode:
            return {
                "id": integration_id,
                "name": name,
                "status": "updated",
                "mode": "no_backend_ui",
            }

        session = await self._get_session()
        async with session.patch(
            f"{settings.api_url}/api/v1/integrations/{integration_id}",
            headers=self._build_headers(),
            params=self._add_org_param(),
            json={"name": name},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


api_client = APIClient()


class DeBankPortfolioClient:
    """Minimal client for the debank-sdk portfolio endpoint.

    Only used to enrich the DEX wallet detail screen with chain breakdown
    and full token names.  All other debank-sdk screens have been removed.
    """

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def get_portfolio(
        self,
        *,
        user_id: int,
        address: str,
        page: int = 0,
    ) -> Dict[str, Any] | None:
        """Return portfolio data for *address* or None on any error.

        Response shape (from debank-sdk):
        {
            "totalUsd": float,
            "totalTokens": int,
            "chains": [{"id": str, "name": str, "totalUsd": float, "tokenCount": int}],
            "tokens": [{"symbol": str, "name": str, "chain": str, "amountUsd": float}],
            "hasPrevPage": bool,
            "hasNextPage": bool,
        }
        """
        base_url = settings.debank_sdk_url
        if not base_url:
            return None
        url = f"{base_url}/users/{user_id}/wallets/{address}/portfolio"
        params: Dict[str, Any] = {}
        if page > 0:
            params["page"] = page
        try:
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.debug("DeBankPortfolioClient.get_portfolio failed: %s", exc)
            return None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


debank_portfolio_client = DeBankPortfolioClient()
