import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from app.core.config import get_settings
from app.core.http import request_proxy_kwargs, session_kwargs
from app.schemas.balance import AssetSchema, AccountBalanceSchema, ServiceBalanceSchema
from app.services.debank_sdk_client import debank_sdk_client

logger = logging.getLogger(__name__)
settings = get_settings()


class OKXWalletService:
    PRIAPI_ACTIVE_POSITIONS_GUEST_URL = (
        "https://web3.okx.com/priapi/v1/dx/market/v2/pnl/active-positions-guest"
    )
    PRIAPI_LIMIT = 100
    PRIAPI_ORDER_BY_CHANGE_TIME = "11"
    PRIAPI_SMALL_BALANCE_THRESHOLD = 1
    _EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
    _SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
    _TRON_ADDRESS_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
    _TON_ADDRESS_RE = re.compile(r"^(EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}$")
    _SOLANA_CHAIN_ID = 501
    _TRON_CHAIN_ID = 195
    _TON_CHAIN_ID = 607

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=settings.request_timeout)
                self._session = aiohttp.ClientSession(**session_kwargs(timeout))
        return self._session

    @classmethod
    def service_name_for(cls, identifier: str) -> str:
        """Return canonical service key: evm_{addr} or sol_{addr}."""
        normalized = (identifier or "").strip().lower()
        family = cls._address_family((identifier or "").strip())
        if family == "solana":
            return f"sol_{normalized}"
        # EVM (and unknown) → evm_ prefix
        return f"evm_{normalized}"

    @staticmethod
    def legacy_service_name_for(identifier: str) -> str:
        return f"okx_wallet_{identifier[:8]}"

    @classmethod
    def is_wallet_address(cls, identifier: str) -> bool:
        return cls._address_family(identifier) is not None

    @classmethod
    def _address_family(cls, identifier: str) -> Optional[str]:
        target = (identifier or "").strip()
        if not target:
            return None
        if cls._EVM_ADDRESS_RE.fullmatch(target):
            return "evm"
        if cls._TRON_ADDRESS_RE.fullmatch(target):
            return "tron"
        if cls._TON_ADDRESS_RE.fullmatch(target):
            return "ton"
        if cls._SOLANA_ADDRESS_RE.fullmatch(target):
            return "solana"
        return None

    @staticmethod
    def _default_headers() -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    @staticmethod
    def _to_service_balance(
        identifier: str, assets: list[AssetSchema]
    ) -> ServiceBalanceSchema:
        total_usd = sum(asset.value_usd for asset in assets)
        account = AccountBalanceSchema(
            account_type="spot",
            assets=assets,
            total_usd=total_usd,
        )
        return ServiceBalanceSchema(
            service=OKXWalletService.service_name_for(identifier),
            accounts=[account] if assets else [],
            assets=assets,
            total_usd=total_usd,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

    @staticmethod
    def _iter_token_candidates(payload: object) -> list[dict[str, object]]:
        stack: list[Any] = [payload]
        tokens: list[dict[str, object]] = []

        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                symbol = (
                    current.get("symbol")
                    or current.get("tokenSymbol")
                    or current.get("coin")
                    or current.get("token")
                    or current.get("tokenName")
                    or current.get("tSym")
                )
                amount = (
                    current.get("coinAmount")
                    or current.get("amount")
                    or current.get("amountNum")
                    or current.get("balance")
                    or current.get("holdingAmount")
                    or current.get("tAmt")
                )
                if symbol is not None and amount is not None:
                    tokens.append(current)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)

        return tokens

    @classmethod
    def _parse_assets(cls, payload: object) -> list[AssetSchema]:
        parsed: dict[str, AssetSchema] = {}
        for token in cls._iter_token_candidates(payload):
            symbol = (
                token.get("symbol")
                or token.get("tokenSymbol")
                or token.get("coin")
                or token.get("token")
                or token.get("tokenName")
                or token.get("tSym")
                or "UNKNOWN"
            )
            amount_raw = (
                token.get("coinAmount")
                or token.get("amount")
                or token.get("amountNum")
                or token.get("balance")
                or token.get("holdingAmount")
                or token.get("tAmt")
                or 0
            )
            value_raw = (
                token.get("currencyAmount")
                or token.get("valueUsd")
                or token.get("usdValue")
                or token.get("amountUsd")
                or token.get("totalValue")
                or token.get("tCurAmt")
                or 0
            )
            try:
                amount = float(amount_raw)
                value_usd = float(value_raw)
            except (TypeError, ValueError):
                continue
            if amount <= 0 and value_usd <= 0:
                continue

            existing = parsed.get(symbol)
            if existing:
                parsed[symbol] = AssetSchema(
                    coin=symbol,
                    amount=existing.amount + amount,
                    value_usd=existing.value_usd + value_usd,
                )
            else:
                parsed[symbol] = AssetSchema(
                    coin=symbol, amount=amount, value_usd=value_usd
                )

        return list(parsed.values())

    async def _get_candidate_chain_ids(self, wallet_address: str) -> list[int]:
        family = self._address_family(wallet_address)
        if family == "solana":
            return [self._SOLANA_CHAIN_ID]
        if family == "ton":
            raise ValueError(
                "TON wallets are not supported by the current live wallet pipeline"
            )
        if family == "tron":
            raise ValueError(
                "TRON wallets are not supported by the current live wallet pipeline"
            )
        raise ValueError("Only Solana uses the live OKX priapi path")

    def _build_priapi_wallet_payload(
        self, wallet_address: str, chain_id: int
    ) -> dict[str, Any]:
        return {
            "walletAddress": wallet_address,
            "limit": self.PRIAPI_LIMIT,
            "orderBy": self.PRIAPI_ORDER_BY_CHANGE_TIME,
            "chainId": chain_id,
            "filterStableOrNativeToken": False,
            "smallBalanceThreshold": self.PRIAPI_SMALL_BALANCE_THRESHOLD,
        }

    async def _fetch_priapi_wallet_balance(
        self, wallet_address: str
    ) -> ServiceBalanceSchema:
        session = await self._get_session()
        aggregated: dict[str, AssetSchema] = {}

        for chain_id in await self._get_candidate_chain_ids(wallet_address):
            try:
                async with session.post(
                    self.PRIAPI_ACTIVE_POSITIONS_GUEST_URL,
                    json=self._build_priapi_wallet_payload(wallet_address, chain_id),
                    headers=self._default_headers(),
                    **request_proxy_kwargs(),
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
            except Exception as exc:
                logger.debug(
                    "OKX priapi active positions failed for %s chain %s: %s",
                    wallet_address,
                    chain_id,
                    exc,
                )
                continue

            positions = (data.get("data") or {}).get("positions", [])
            for asset in self._parse_assets(positions):
                current = aggregated.get(asset.coin)
                if current:
                    aggregated[asset.coin] = AssetSchema(
                        coin=asset.coin,
                        amount=current.amount + asset.amount,
                        value_usd=current.value_usd + asset.value_usd,
                    )
                else:
                    aggregated[asset.coin] = asset

        assets = list(aggregated.values())
        if assets:
            return self._to_service_balance(wallet_address, assets)

        raise ValueError(f"OKX priapi returned no live assets for {wallet_address}")

    async def _fetch_evm_wallet_balance(
        self, wallet_address: str
    ) -> ServiceBalanceSchema:
        balance = await debank_sdk_client.fetch_wallet_balance(wallet_address)
        return balance.model_copy(
            update={
                "service": self.service_name_for(wallet_address),
                "updated_at": datetime.now(timezone.utc),
            }
        )

    async def fetch_wallet_balance(self, identifier: str) -> ServiceBalanceSchema:
        target = (identifier or "").strip()
        if not target:
            raise ValueError("wallet identifier is required")

        family = self._address_family(target)
        if family == "evm":
            return await self._fetch_evm_wallet_balance(target)
        if family == "solana":
            return await self._fetch_priapi_wallet_balance(target)
        if family in {"ton", "tron"}:
            # Delegate to TronGrid / tonapi.io pipeline (no API key required)
            from app.services.tron_ton_service import tron_ton_service

            return await tron_ton_service.fetch_wallet_balance(target)
        if settings.okx_wallet_legacy_enabled:
            raise ValueError(
                "Legacy OKX wallet account-id mode was archived and must not be used in live refreshes"
            )
        raise ValueError(
            "Wallet address is required; legacy OKX wallet account ids are archived"
        )

    async def fetch_all_wallets(
        self, identifiers: Optional[list[str]] = None
    ) -> dict[str, ServiceBalanceSchema | Exception]:
        if identifiers is None:
            identifiers = settings.okx_wallet_targets

        results = {}
        for identifier in identifiers:
            try:
                result = await asyncio.wait_for(
                    self.fetch_wallet_balance(identifier),
                    timeout=settings.request_timeout,
                )
                results[self.service_name_for(identifier)] = result
            except Exception as exc:
                logger.error("Failed to fetch OKX wallet %s: %s", identifier, exc)
                results[self.service_name_for(identifier)] = exc

        return results

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        await debank_sdk_client.close()


okx_wallet_service = OKXWalletService()
