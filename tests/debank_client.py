"""DeBank API client to fetch portfolio data and blockchain balances."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import aiohttp

from app.core.config import get_settings
from app.schemas.balance import AssetSchema, AccountBalanceSchema, ServiceBalanceSchema


logger = logging.getLogger(__name__)
settings = get_settings()


# Import signature generator if available
try:
    from tests.debank_signature import DeBankSignatureGenerator

    SIGNATURE_AVAILABLE = True
except ImportError:
    SIGNATURE_AVAILABLE = False
    logger.warning("DeBank signature generator not available - using unsigned requests")


class DeBankClient:
    """Client for interacting with DeBank API to fetch wallet portfolio data."""

    BASE_URL = "https://api.debank.com"

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session with proper timeout."""
        async with self._lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=settings.request_timeout)
                self._session = aiohttp.ClientSession(
                    timeout=timeout,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    },
                )
        return self._session

    async def fetch_portfolio(self, wallet_address: str) -> Dict[str, Any]:
        """Fetch portfolio data for a given wallet address."""
        session = await self._get_session()

        url = f"{self.BASE_URL}/portfolio_v2/list"
        params = {"id": wallet_address.lower()}

        try:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()

                if data.get("error_code") != 0:
                    raise Exception(f"DeBank API error: {data.get('error_msg')}")

                return data.get("data", {})
        except Exception as e:
            logger.error(f"Error fetching DeBank portfolio for {wallet_address}: {e}")
            raise

    async def fetch_tokens(self, wallet_address: str) -> Dict[str, Any]:
        """Fetch token balances for a given wallet address."""
        session = await self._get_session()

        url = f"{self.BASE_URL}/token/balance_list"
        params = {"id": wallet_address.lower(), "is_all": "true"}

        try:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()

                if data.get("error_code") != 0:
                    raise Exception(f"DeBank API error: {data.get('error_msg')}")

                return data.get("data", {})
        except Exception as e:
            logger.error(f"Error fetching DeBank tokens for {wallet_address}: {e}")
            raise

    async def fetch_wallet_balance(self, wallet_address: str) -> ServiceBalanceSchema:
        """Fetch and format wallet balance data for the balance tracker."""
        try:
            # Fetch portfolio data
            portfolio_data = await self.fetch_portfolio(wallet_address)

            assets: List[AssetSchema] = []
            total_usd = 0.0

            # Parse portfolio items
            if portfolio_data:
                for chain_portfolio in portfolio_data:
                    if (
                        isinstance(chain_portfolio, dict)
                        and "token_list" in chain_portfolio
                    ):
                        token_list = chain_portfolio["token_list"]
                        if isinstance(token_list, list):
                            for token in token_list:
                                if isinstance(token, dict):
                                    coin = token.get("symbol", "UNKNOWN")
                                    amount = float(token.get("amount", 0))
                                    value_usd = float(token.get("usd_value", 0))

                                    if amount > 0 or value_usd > 0:
                                        assets.append(
                                            AssetSchema(
                                                coin=f"{coin}_{token.get('chain', 'unknown')}",
                                                amount=amount,
                                                value_usd=value_usd,
                                            )
                                        )
                                        total_usd += value_usd

            # Also fetch direct token balances if portfolio data is incomplete
            if not assets:
                token_data = await self.fetch_tokens(wallet_address)
                if isinstance(token_data, list):
                    for token in token_data:
                        if isinstance(token, dict):
                            coin = token.get("symbol", "UNKNOWN")
                            amount = float(token.get("amount", 0))
                            value_usd = float(
                                token.get("price", 0) * token.get("amount", 0)
                            )

                            if amount > 0 or value_usd > 0:
                                assets.append(
                                    AssetSchema(
                                        coin=f"{coin}_{token.get('chain', 'unknown')}",
                                        amount=amount,
                                        value_usd=value_usd,
                                    )
                                )
                                total_usd += value_usd

            service_name = f"debank_{wallet_address[:8].lower()}"

            # Normalize as spot account for consistency with other services
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
            logger.error(f"Error fetching DeBank balance for {wallet_address}: {e}")
            raise

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


debank_client = DeBankClient()
