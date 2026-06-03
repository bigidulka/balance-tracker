from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiohttp

from app.core.http import request_proxy_kwargs, session_kwargs
from app.schemas.balance import AccountBalanceSchema, AssetSchema, ServiceBalanceSchema


class CryptoBotAppClientError(RuntimeError):
    pass


class CryptoBotAppClient:
    def __init__(self, *, base_url: str = "https://pay.crypt.bot/api") -> None:
        self.base_url = base_url.rstrip("/")

    async def _request(self, api_token: str, method_name: str, payload: dict[str, Any] | None = None) -> Any:
        token = str(api_token or "").strip()
        if not token:
            raise CryptoBotAppClientError("Crypto Bot app token is required")

        headers = {
            "Crypto-Pay-API-Token": token,
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession(**session_kwargs(aiohttp.ClientTimeout(total=20))) as session:
            async with session.post(
                f"{self.base_url}/{method_name}",
                headers=headers,
                json=payload or {},
                **request_proxy_kwargs(),
            ) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    raise CryptoBotAppClientError(
                        f"Crypto Bot API {method_name} failed with status {response.status}: {data}"
                    )
                if not isinstance(data, dict) or not data.get("ok"):
                    raise CryptoBotAppClientError(
                        f"Crypto Bot API {method_name} returned error: {data}"
                    )
                return data.get("result")

    async def get_me(self, api_token: str) -> dict[str, Any]:
        result = await self._request(api_token, "getMe")
        if not isinstance(result, dict):
            raise CryptoBotAppClientError("Crypto Bot API getMe returned invalid payload")
        return result

    async def get_balances(self, api_token: str) -> list[dict[str, Any]]:
        result = await self._request(api_token, "getBalances")
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict) and isinstance(result.get("items"), list):
            return [item for item in result["items"] if isinstance(item, dict)]
        raise CryptoBotAppClientError("Crypto Bot API getBalances returned invalid payload")

    async def verify_token(self, api_token: str) -> dict[str, Any]:
        return await self.get_me(api_token)

    async def fetch_balance(
        self,
        api_token: str,
        *,
        integration_id: int | None = None,
        service: str = "cryptobot",
    ) -> ServiceBalanceSchema:
        balances = await self.get_balances(api_token)
        assets: list[AssetSchema] = []

        for item in balances:
            coin = str(
                item.get("currency_code")
                or item.get("asset")
                or item.get("code")
                or item.get("currency")
                or ""
            ).strip()
            if not coin:
                continue

            available = float(item.get("available") or item.get("amount") or item.get("balance") or 0.0)
            onhold = float(item.get("onhold") or item.get("on_hold") or item.get("freeze") or 0.0)
            total_amount = available + onhold
            usd_rate = float(item.get("usd_rate") or item.get("rate") or item.get("price_usd") or 0.0)
            assets.append(
                AssetSchema(
                    coin=coin,
                    amount=total_amount,
                    value_usd=total_amount * usd_rate,
                )
            )

        total_usd = sum(asset.value_usd for asset in assets)
        return ServiceBalanceSchema(
            integration_id=integration_id,
            service=service,
            accounts=[
                AccountBalanceSchema(
                    account_type="app",
                    assets=assets,
                    total_usd=total_usd,
                )
            ],
            assets=assets,
            total_usd=total_usd,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )


crypto_bot_app_client = CryptoBotAppClient()
