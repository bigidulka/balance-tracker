import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from app.core.config import get_settings
from app.schemas.balance import AssetSchema, AccountBalanceSchema, ServiceBalanceSchema

settings = get_settings()


class DeBankSdkClient:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=settings.debank_sdk_timeout_seconds)
                self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _request_json(self, path: str) -> dict[str, Any]:
        session = await self._get_session()
        async with session.get(f"{settings.debank_sdk_base_url}{path}") as response:
            payload = await response.json()
            if response.status >= 400:
                raise RuntimeError(payload.get("error", f"DeBank SDK request failed: {response.status}"))
            if not isinstance(payload, dict):
                raise RuntimeError("DeBank SDK returned unexpected response shape")
            return payload

    @staticmethod
    def _build_assets(tokens: list[dict[str, Any]]) -> list[AssetSchema]:
        assets: list[AssetSchema] = []
        for token in tokens:
            if not isinstance(token, dict):
                continue
            symbol = str(token.get("symbol") or token.get("name") or token.get("id") or "UNKNOWN")
            chain = str(token.get("chain") or "unknown")
            amount = float(token.get("amount") or 0)
            amount_usd = float(token.get("amountUsd") or 0)
            if amount <= 0 and amount_usd <= 0:
                continue
            assets.append(
                AssetSchema(
                    coin=f"{symbol}_{chain}",
                    amount=amount,
                    value_usd=amount_usd,
                )
            )
        return assets

    async def fetch_wallet_balance(self, wallet_address: str) -> ServiceBalanceSchema:
        payload = await self._request_json(f"/wallets/{quote(wallet_address, safe='')}/balance")
        assets = self._build_assets(payload.get("tokens", []))
        total_usd = float(payload.get("totalUsd") or 0.0)
        account = AccountBalanceSchema(
            account_type="spot",
            assets=assets,
            total_usd=total_usd,
        )
        return ServiceBalanceSchema(
            service=f"debank_sdk_{wallet_address[:8].lower()}",
            accounts=[account] if assets else [],
            assets=assets,
            total_usd=total_usd,
            updated_at=datetime.fromtimestamp(
                float(payload.get("fetchedAt", 0)) / 1000,
                tz=timezone.utc,
            )
            if payload.get("fetchedAt")
            else datetime.now(timezone.utc),
            actual=True,
        )

    async def healthcheck(self) -> dict[str, Any]:
        return await self._request_json("/health")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


debank_sdk_client = DeBankSdkClient()

