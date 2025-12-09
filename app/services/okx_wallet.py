import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from app.core.config import get_settings
from app.schemas.balance import AssetSchema, AccountBalanceSchema, ServiceBalanceSchema

logger = logging.getLogger(__name__)
settings = get_settings()


class OKXWalletService:
    BASE_URL = "https://web3.okx.com/priapi/v2/wallet/asset/profile/all"

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=settings.request_timeout)
                self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def fetch_wallet_balance(self, account_id: str) -> ServiceBalanceSchema:
        session = await self._get_session()

        payload = {
            "userUniqueId": "",
            "hideValueless": False,
            "accountIds": [account_id],
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        try:
            async with session.post(
                self.BASE_URL,
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                data = await response.json()

                assets: list[AssetSchema] = []
                total_usd = 0.0

                if data.get("data"):
                    for wallet_data in data["data"]:
                        if wallet_data.get("tokens"):
                            for token in wallet_data["tokens"]:
                                coin = token.get("symbol", "UNKNOWN")
                                amount = float(token.get("coinAmount", 0))
                                value_usd = float(token.get("currencyAmount", 0))

                                if amount > 0:
                                    assets.append(
                                        AssetSchema(
                                            coin=coin,
                                            amount=amount,
                                            value_usd=value_usd,
                                        )
                                    )
                                    total_usd += value_usd

                service_name = f"okx_wallet_{account_id[:8]}"

                # Web3 кошелёк нормализуем как spot
                account = AccountBalanceSchema(
                    account_type="spot",
                    assets=assets,
                    total_usd=total_usd,
                )

                return ServiceBalanceSchema(
                    service=service_name,
                    accounts=[account] if assets else [],
                    assets=assets,
                    total_usd=total_usd,
                    updated_at=datetime.now(timezone.utc),
                    actual=True,
                )
        except Exception as e:
            logger.error(f"Error fetching OKX wallet balance for {account_id}: {e}")
            raise

    async def fetch_all_wallets(
        self, account_ids: Optional[list[str]] = None
    ) -> dict[str, ServiceBalanceSchema | Exception]:
        if account_ids is None:
            account_ids = settings.okx_wallet_accounts

        results = {}

        for account_id in account_ids:
            try:
                result = await asyncio.wait_for(
                    self.fetch_wallet_balance(account_id),
                    timeout=settings.request_timeout,
                )
                results[f"okx_wallet_{account_id[:8]}"] = result
            except Exception as e:
                logger.error(f"Failed to fetch OKX wallet {account_id}: {e}")
                results[f"okx_wallet_{account_id[:8]}"] = e

        return results

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


okx_wallet_service = OKXWalletService()
