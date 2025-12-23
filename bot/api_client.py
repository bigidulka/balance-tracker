import aiohttp
from typing import Optional, Dict, Any
from bot.config import API_URL


class APIClient:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

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
        async with session.get(f"{API_URL}{endpoint}") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_health(self) -> Dict[str, Any]:
        session = await self._get_session()
        async with session.get(f"{API_URL}/api/v1/health") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def refresh(self) -> Dict[str, Any]:
        """Trigger full refresh from all exchanges (slow)"""
        session = await self._get_session()
        # Longer timeout for full refresh
        timeout = aiohttp.ClientTimeout(total=120)
        async with session.post(f"{API_URL}/api/v1/refresh", timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_transactions(
        self,
        service: Optional[str] = None,
        tx_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Get transactions history"""
        session = await self._get_session()
        params = {"limit": limit}
        if service:
            params["service"] = service
        if tx_type:
            params["tx_type"] = tx_type
        if status:
            params["status"] = status

        async with session.get(f"{API_URL}/api/v1/transactions", params=params) as resp:
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
            f"{API_URL}/api/v1/transactions/refresh",
            params={"since_hours": since_hours},
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


api_client = APIClient()
