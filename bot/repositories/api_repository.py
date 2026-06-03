"""Repository wrapper for bot API access."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import time

from bot.api_client import api_client


class ApiRepository:
    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._cache_ts: dict[str, float] = {}
        self._ttl_seconds: dict[str, float] = {
            "dashboard_summary": 3.0,
            "integrations": 10.0,
            "billing_current": 60.0,
            "capabilities": 60.0,
        }

    def _cache_get(self, key: str) -> Any | None:
        ts = self._cache_ts.get(key, 0.0)
        ttl = self._ttl_seconds.get(key, 0.0)
        if ttl > 0 and time.monotonic() - ts < ttl and key in self._cache:
            return self._cache[key]
        return None

    def _cache_put(self, key: str, value: Any) -> Any:
        self._cache[key] = value
        self._cache_ts[key] = time.monotonic()
        return value

    def invalidate(self, *keys: str) -> None:
        target_keys = keys or tuple(self._cache.keys())
        for key in target_keys:
            self._cache.pop(key, None)
            self._cache_ts.pop(key, None)
    async def get_capabilities(self) -> dict[str, Any]:
        cached = self._cache_get("capabilities")
        if isinstance(cached, dict):
            return cached
        payload = await api_client.get_capabilities()
        value = payload if isinstance(payload, dict) else {}
        return self._cache_put("capabilities", value)

    async def get_billing_current(self) -> dict[str, Any]:
        cached = self._cache_get("billing_current")
        if isinstance(cached, dict):
            return cached
        payload = await api_client.get_billing_current()
        value = payload if isinstance(payload, dict) else {}
        return self._cache_put("billing_current", value)

    async def get_billing_plans(self) -> dict[str, Any]:
        payload = await api_client.get_billing_plans()
        return payload if isinstance(payload, dict) else {}

    async def get_billing_balance(self) -> dict[str, Any]:
        payload = await api_client.get_billing_balance()
        return payload if isinstance(payload, dict) else {}

    async def list_payment_invoices(self) -> dict[str, Any]:
        payload = await api_client.list_payment_invoices()
        return payload if isinstance(payload, dict) else {}

    async def create_payment_invoice(
        self,
        amount_usd: float | None = None,
        *,
        invoice_type: str = "balance_topup",
        plan_code: str | None = None,
    ) -> dict[str, Any]:
        payload = await api_client.create_payment_invoice(
            amount_usd,
            invoice_type=invoice_type,
            plan_code=plan_code,
        )
        return payload if isinstance(payload, dict) else {}

    async def switch_billing_plan(self, plan_code: str) -> dict[str, Any]:
        payload = await api_client.switch_billing_plan(plan_code)
        return payload if isinstance(payload, dict) else {}

    async def refresh_payment_invoice(self, invoice_id: int) -> dict[str, Any]:
        payload = await api_client.refresh_payment_invoice(invoice_id)
        return payload if isinstance(payload, dict) else {}

    async def redeem_promo_code(self, code: str) -> dict[str, Any]:
        payload = await api_client.redeem_promo_code(code)
        return payload if isinstance(payload, dict) else {}

    async def admin_list_users(self, *, limit: int, offset: int) -> dict[str, Any]:
        payload = await api_client.admin_list_users(limit=limit, offset=offset)
        return payload if isinstance(payload, dict) else {}

    async def admin_get_user_detail(
        self, *, user_id: int, organization_id: int
    ) -> dict[str, Any]:
        payload = await api_client.admin_get_user_detail(
            user_id=user_id,
            organization_id=organization_id,
        )
        return payload if isinstance(payload, dict) else {}

    async def get_dashboard_summary(self) -> dict[str, Any]:
        cached = self._cache_get("dashboard_summary")
        if isinstance(cached, dict):
            return cached
        payload = await api_client.get_dashboard_summary()
        value = payload if isinstance(payload, dict) else {}
        return self._cache_put("dashboard_summary", value)

    async def get_balances(self, *, force_update_cache: bool = False) -> dict[str, Any]:
        from bot.services.balance import get_balance_data

        return await get_balance_data(force_update_cache=force_update_cache)

    async def refresh_balances(self) -> dict[str, Any]:
        payload = await api_client.refresh()
        self.invalidate("dashboard_summary")
        from bot.services.balance import invalidate_balance_cache

        # Refresh endpoint may be queued; drop local hot cache so next render can
        # pick up newer cached backend state once worker finishes.
        invalidate_balance_cache(api_client.current_identity_cache_key())
        return payload if isinstance(payload, dict) else {}

    async def get_transactions(
        self,
        *,
        limit: int,
        offset: int,
        tx_type: str | None,
        tx_status: str | None,
        tx_service: str | None,
        integration_id: int | None = None,
        start_date: datetime,
    ) -> dict[str, Any]:
        payload = await api_client.get_transactions(
            limit=limit,
            offset=offset,
            tx_type=tx_type,
            status=tx_status,
            service=tx_service,
            integration_id=integration_id,
            start_date=start_date,
        )
        return payload if isinstance(payload, dict) else {}

    async def refresh_transactions(self, since_hours: int) -> dict[str, Any]:
        payload = await api_client.refresh_transactions(since_hours=since_hours)
        return payload if isinstance(payload, dict) else {}

    async def get_integrations(self) -> list[dict[str, Any]]:
        cached = self._cache_get("integrations")
        if isinstance(cached, list):
            return cached
        payload = await api_client.get_integrations(include_inactive=True)
        value = payload if isinstance(payload, list) else []
        return self._cache_put("integrations", value)

    async def verify_integration_credentials(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        response = await api_client.verify_integration_credentials(payload)
        return response if isinstance(response, dict) else {}

    async def create_integration(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await api_client.create_integration(payload)
        self.invalidate("integrations", "dashboard_summary", "billing_current", "capabilities")
        return response if isinstance(response, dict) else {}

    async def activate_integration(self, integration_id: int) -> dict[str, Any]:
        payload = await api_client.activate_integration(integration_id)
        self.invalidate("integrations", "dashboard_summary")
        return payload if isinstance(payload, dict) else {}

    async def deactivate_integration(self, integration_id: int) -> dict[str, Any]:
        payload = await api_client.deactivate_integration(integration_id)
        self.invalidate("integrations", "dashboard_summary")
        return payload if isinstance(payload, dict) else {}

    async def refresh_integration(self, integration_id: int) -> dict[str, Any]:
        payload = await api_client.refresh_integration(integration_id)
        self.invalidate("dashboard_summary")
        return payload if isinstance(payload, dict) else {}

    async def delete_integration(self, integration_id: int) -> dict[str, Any]:
        payload = await api_client.delete_integration(integration_id)
        self.invalidate("integrations", "dashboard_summary", "billing_current", "capabilities")
        return payload if isinstance(payload, dict) else {}

    async def update_integration(
        self, integration_id: int, name: str
    ) -> dict[str, Any]:
        payload = await api_client.update_integration(integration_id, name)
        return payload if isinstance(payload, dict) else {}
