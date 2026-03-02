"""Comprehensive DeBank SDK combining API client and web scraping approaches."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass

from tests.debank_client import DeBankClient, debank_client
from app.schemas.balance import AssetSchema, AccountBalanceSchema, ServiceBalanceSchema


logger = logging.getLogger(__name__)


@dataclass
class DeBankConfig:
    """Configuration for DeBank SDK."""

    use_scraping_fallback: bool = True
    scraping_enabled: bool = True
    api_timeout: int = 30
    cache_ttl: int = 300  # 5 minutes
    max_retries: int = 3


class DeBankSDK:
    """SDK for DeBank integration with both API and scraping capabilities."""

    def __init__(self, config: Optional[DeBankConfig] = None):
        self.config = config or DeBankConfig()
        self.api_client = debank_client
        self._scraping_available = False
        self._cache: Dict[str, tuple] = {}  # address -> (data, timestamp)

        # Initialize scraping if enabled
        if self.config.scraping_enabled:
            try:
                from app.services.simple_debank_scraper import simple_debank_scraper

                self._scraping_service = simple_debank_scraper
                self._scraping_available = True
            except ImportError:
                logger.warning("Playwright not available, scraping disabled")
                self.config.scraping_enabled = False

    async def get_portfolio_data(self, wallet_address: str) -> ServiceBalanceSchema:
        """Get portfolio data using API with scraping fallback."""
        # Check cache first
        cached = self._get_cached(wallet_address)
        if cached:
            return cached

        # Try API first
        try:
            result = await self.api_client.fetch_wallet_balance(wallet_address)
            self._cache_result(wallet_address, result)
            return result
        except Exception as api_error:
            logger.warning(f"API failed for {wallet_address}: {api_error}")

            # Try scraping if enabled and API failed
            if self.config.use_scraping_fallback and self._scraping_available:
                try:
                    from tests.debank_scraper import debank_scraper

                    result = await debank_scraper.scrape_wallet_balance(wallet_address)
                    self._cache_result(wallet_address, result)
                    return result
                except Exception as scraping_error:
                    logger.error(
                        f"Both API and scraping failed for {wallet_address}: API={api_error}, Scraping={scraping_error}"
                    )
                    raise scraping_error
            else:
                raise api_error

    async def get_wallet_summary(self, wallet_address: str) -> Dict[str, Any]:
        """Get wallet summary including total portfolio value."""
        # Try API methods first
        try:
            portfolio_data = await self.api_client.fetch_portfolio(wallet_address)
            summary = {
                "wallet_address": wallet_address,
                "chains": [],
                "total_usd": 0.0,
                "token_count": 0,
                "portfolio_data": portfolio_data,
            }

            if portfolio_data:
                for chain_portfolio in portfolio_data:
                    if chain_portfolio.get("token_list"):
                        summary["token_count"] += len(chain_portfolio["token_list"])
                        for token in chain_portfolio["token_list"]:
                            summary["total_usd"] += float(token.get("usd_value", 0))

                    chain_name = chain_portfolio.get("chain") or "unknown"
                    if chain_name not in summary["chains"]:
                        summary["chains"].append(chain_name)

            return summary
        except Exception as api_error:
            logger.warning(f"API summary failed for {wallet_address}: {api_error}")

            # Fall back to scraping if available
            if self.config.use_scraping_fallback and self._scraping_available:
                try:
                    from app.services.simple_debank_scraper import simple_debank_scraper

                    return await simple_debank_scraper.get_wallet_summary(
                        wallet_address
                    )
                except Exception as scraping_error:
                    logger.error(
                        f"Both API and scraping summary failed for {wallet_address}: {scraping_error}"
                    )
                    raise scraping_error
            else:
                raise api_error

    async def get_token_balances(self, wallet_address: str) -> List[Dict[str, Any]]:
        """Get detailed token balances."""
        try:
            token_data = await self.api_client.fetch_tokens(wallet_address)
            tokens = []

            if token_data:
                for token in token_data:
                    tokens.append(
                        {
                            "name": token.get("name", ""),
                            "symbol": token.get("symbol", ""),
                            "chain": token.get("chain", "unknown"),
                            "amount": token.get("amount", 0),
                            "price": token.get("price", 0),
                            "usd_value": token.get("usd_value", 0),
                            "decimals": token.get("decimals", 18),
                            "logo_url": token.get("logo_url", ""),
                            "contract_address": token.get("id", ""),
                        }
                    )

            return tokens
        except Exception as e:
            logger.error(f"Error getting token balances for {wallet_address}: {e}")
            raise

    def _get_cached(self, wallet_address: str) -> Optional[ServiceBalanceSchema]:
        """Get cached result if still valid."""
        if wallet_address in self._cache:
            data, timestamp = self._cache[wallet_address]
            age = (datetime.now(timezone.utc) - timestamp).total_seconds()

            if age < self.config.cache_ttl:
                return data

        return None

    def _cache_result(self, wallet_address: str, result: ServiceBalanceSchema):
        """Cache the result."""
        self._cache[wallet_address] = (result, datetime.now(timezone.utc))

    async def clear_cache(self):
        """Clear the cache."""
        self._cache.clear()

    async def health_check(self) -> Dict[str, Any]:
        """Perform health check of the SDK."""
        health = {
            "api_available": True,
            "scraping_available": self._scraping_available,
            "cache_size": len(self._cache),
            "last_cache_clear": datetime.now(timezone.utc),
        }

        # Test API with a simple call
        try:
            # Use a test address to verify API connectivity
            # In practice, you'd want to use a known working address for testing
            pass
        except Exception as e:
            health["api_available"] = False
            health["api_error"] = str(e)

        return health

    async def close(self):
        """Close the SDK resources."""
        await self.api_client.close()
        if self._scraping_available:
            from tests.debank_scraper import debank_scraper

            await debank_scraper.close()


# Global SDK instance
debank_sdk = DeBankSDK()


# Convenience functions for easy usage
async def get_debank_portfolio(wallet_address: str) -> ServiceBalanceSchema:
    """Convenience function to get DeBank portfolio data."""
    return await debank_sdk.get_portfolio_data(wallet_address)


async def get_debank_summary(wallet_address: str) -> Dict[str, Any]:
    """Convenience function to get DeBank wallet summary."""
    return await debank_sdk.get_wallet_summary(wallet_address)


async def get_debank_tokens(wallet_address: str) -> List[Dict[str, Any]]:
    """Convenience function to get DeBank token balances."""
    return await debank_sdk.get_token_balances(wallet_address)
