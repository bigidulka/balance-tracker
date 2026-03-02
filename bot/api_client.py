import aiohttp
from datetime import datetime
from typing import Optional, Dict, Any

from bot.config import settings


class APIClient:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    def _build_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if settings.api_token:
            headers["Authorization"] = f"Bearer {settings.api_token}"
        if settings.api_org_id:
            headers["X-Organization-Id"] = settings.api_org_id
            headers["X-Org-Id"] = settings.api_org_id
        return headers

    def _add_org_param(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        merged = dict(params or {})
        if settings.api_org_id and "organization_id" not in merged:
            merged["organization_id"] = settings.api_org_id
        return merged

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)  # Fast timeout for cached data
            )
        return self._session

    async def get_balances(self, cached: bool = True) -> Dict[str, Any]:
        """Get balances from API

        Args:
            cached: If True, get from DB cache (fast). If False, may fetch live.
        """
        session = await self._get_session()
        endpoint = "/api/v1/balances/cached" if cached else "/api/v1/balances"
        async with session.get(
            f"{settings.api_url}{endpoint}",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_dashboard_summary(self) -> Dict[str, Any]:
        session = await self._get_session()
        async with session.get(
            f"{settings.api_url}/api/v1/dashboard/summary",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_health(self) -> Dict[str, Any]:
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
        tx_type: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get transactions history"""
        session = await self._get_session()
        params = {"limit": limit, "offset": offset}
        if service:
            params["service"] = service
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

    async def get_capabilities(self) -> Dict[str, Any]:
        """Get capabilities/entitlements payload if API provides it."""
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

        if last_error:
            raise last_error

        return {}

    async def get_integrations(self, include_inactive: bool = True) -> Dict[str, Any]:
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
        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/integrations/{integration_id}/deactivate",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def activate_integration(self, integration_id: int) -> Dict[str, Any]:
        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/integrations/{integration_id}/activate",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def refresh_integration(self, integration_id: int) -> Dict[str, Any]:
        session = await self._get_session()
        async with session.post(
            f"{settings.api_url}/api/v1/integrations/{integration_id}/refresh",
            headers=self._build_headers(),
            params=self._add_org_param(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


api_client = APIClient()
