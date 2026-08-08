"""
SUI blockchain wallet balance service.

Uses a SUI JSON-RPC endpoint (configurable via settings.sui_rpc_url).
The SUI Foundation's own public fullnode retired JSON-RPC at the end of
July 2026 in favor of GraphQL/gRPC, so this defaults to a third-party
node (OKX nodeone) that still serves the legacy suix_* JSON-RPC methods.

Balance strategy
----------------
- suix_getAllBalances  → returns all coin types held by the address
- SUI native coin: ONLY 0x2::sui::SUI (MIST, 1 SUI = 1_000_000_000 MIST)
  Any other coin_type containing "sui" in the symbol is a spam/wrapped token
  and is included with value_usd=0.
- Stablecoins: static $1.00 for known USDC/USDT coin types.
- All other tokens: priced via 3-tier enrichment
    Tier 1: DexScreener (batch, fast)
    Tier 2: GeckoTerminal (batch, rate-limited)
    Tier 3: DEXTools (per-address, last resort)
- Tokens with zero balance are skipped.
"""

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from app.core.config import get_settings
from app.core.http import request_proxy_kwargs, session_kwargs
from app.schemas.balance import AssetSchema, AccountBalanceSchema, ServiceBalanceSchema, TransactionSchema
from app.services.price_enrichment import TokenPrice, enrich_prices

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUI_RPC_URL = "https://fullnode.mainnet.sui.io"

_SUI_MIST_PER_SUI = 1_000_000_000  # 1 SUI = 1e9 MIST

# SUI address: 0x followed by exactly 64 hex chars (32 bytes)
_SUI_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

# Only this specific coin type is genuine SUI
_SUI_COIN_TYPE = "0x2::sui::SUI"

# Known stablecoins: coin_type -> (symbol, decimals, usd_price)
# Only authoritative coin types (not arbitrary forks) are listed here.
_STABLE_COINS: dict[str, tuple[str, int, float]] = {
    # USDC (Wormhole)
    "0x5d4b302506645c37ff133b98c4b50a5ae14841659738d6d733d59d0d217a93bf::coin::COIN": (
        "USDC",
        6,
        1.0,
    ),
    # USDT (Wormhole)
    "0xc060006111016b8a020ad5b33834984a437aaa7d3c74c18e09a95d48aceab08c::coin::COIN": (
        "USDT",
        6,
        1.0,
    ),
    # USDC native (Circle)
    "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC": (
        "USDC",
        6,
        1.0,
    ),
    # SUSD
    "0x84d7aeef42d38a5ffc3ccef853e1b82e4958659d16a7de736a29c55fbbeb0114::susd::SUSD": (
        "SUSD",
        6,
        1.0,
    ),
}

# Minimum token amount to include in the output (dust filter)
_DUST_MIN_AMOUNT = 1e-9


class SuiService:
    """Fetch wallet balances for SUI blockchain addresses."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=settings.request_timeout)
                self._session = aiohttp.ClientSession(**session_kwargs(timeout))
        return self._session

    @staticmethod
    def _default_headers() -> dict[str, str]:
        return {"Content-Type": "application/json", "Accept": "application/json"}

    # ------------------------------------------------------------------
    # Address detection
    # ------------------------------------------------------------------

    @classmethod
    def is_sui_address(cls, address: str) -> bool:
        """Return True if address looks like a valid SUI address (0x + 64 hex)."""
        return bool(_SUI_ADDRESS_RE.fullmatch((address or "").strip()))

    @staticmethod
    def service_name_for(address: str) -> str:
        normalized = (address or "").strip().lower()
        return f"sui_{normalized}"

    # ------------------------------------------------------------------
    # RPC helpers
    # ------------------------------------------------------------------

    async def _rpc_call(self, method: str, params: list[Any]) -> Any:
        """Execute a single SUI JSON-RPC call and return the 'result' value."""
        session = await self._get_session()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        try:
            async with session.post(
                _SUI_RPC_URL,
                json=payload,
                headers=self._default_headers(),
                **request_proxy_kwargs(),
            ) as resp:
                resp.raise_for_status()
                data: dict[str, Any] = await resp.json()
        except Exception as exc:
            raise ValueError(f"SUI RPC call '{method}' failed: {exc}") from exc

        if "error" in data:
            raise ValueError(f"SUI RPC error for '{method}': {data['error']}")

        return data.get("result")

    # ------------------------------------------------------------------
    # Symbol / package extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_symbol(coin_type: str) -> str:
        """
        Best-effort symbol extraction from SUI coin type string.

        Examples:
          0x2::sui::SUI                       -> SUI
          0xabc...::coin::COIN                -> COIN
          0xabc...::some_module::SomeCoin     -> SomeCoin
        """
        parts = coin_type.split("::")
        if len(parts) >= 3:
            return parts[-1]
        pkg = (parts[0] or "")[-8:]
        return pkg + "..."

    @staticmethod
    def _extract_package(coin_type: str) -> str:
        """Return the package address (the leading 0x... part)."""
        return coin_type.split("::")[0] if "::" in coin_type else coin_type

    # ------------------------------------------------------------------
    # Core fetch
    # ------------------------------------------------------------------

    async def fetch_sui_balance(self, address: str) -> ServiceBalanceSchema:
        """
        Fetch all coin balances for a SUI address.

        Only 0x2::sui::SUI is treated as genuine SUI.  All other coin types
        that happen to share the "SUI" symbol (common spam pattern on SUI
        mainnet) are included at value_usd=0 so the user can see them but
        they don't inflate the total.
        """
        address = address.strip()
        session = await self._get_session()

        # Fetch all balances — one RPC call
        result: list[dict[str, Any]] | None = await self._rpc_call(
            "suix_getAllBalances", [address]
        )

        if not result:
            return self._to_service_balance(address, [])

        # ----------------------------------------------------------------
        # Bucket raw entries
        # ----------------------------------------------------------------
        sui_raw: int = 0
        stable_entries: list[tuple[str, int, float]] = []  # (symbol, raw, usd_price)
        unknown_entries: list[tuple[str, int, str]] = []  # (symbol, raw, coin_type)

        for entry in result:
            coin_type: str = entry.get("coinType") or ""
            if not coin_type:
                continue
            try:
                raw = int(entry.get("totalBalance") or 0)
            except (ValueError, TypeError):
                continue
            if raw <= 0:
                continue

            if coin_type == _SUI_COIN_TYPE:
                sui_raw += raw
            elif coin_type in _STABLE_COINS:
                sym, dec, price = _STABLE_COINS[coin_type]
                amount = raw / (10**dec)
                if amount >= _DUST_MIN_AMOUNT:
                    stable_entries.append((sym, raw, price))
            else:
                sym = self._extract_symbol(coin_type)
                amount = raw / _SUI_MIST_PER_SUI
                if amount >= _DUST_MIN_AMOUNT:
                    unknown_entries.append((sym, raw, coin_type))

        # ----------------------------------------------------------------
        # Price enrichment: SUI + unknown tokens concurrently
        # ----------------------------------------------------------------
        sui_token = TokenPrice(address=_SUI_COIN_TYPE, chain="sui")

        # Build TokenPrice list for unknown tokens (dedupe by package address)
        seen_packages: dict[str, TokenPrice] = {}
        ct_to_tp: dict[str, TokenPrice] = {}
        for sym, raw, coin_type in unknown_entries:
            pkg = self._extract_package(coin_type)
            if pkg not in seen_packages:
                tp = TokenPrice(
                    address=pkg, chain="sui", extra={"coin_type": coin_type}
                )
                seen_packages[pkg] = tp
            ct_to_tp[coin_type] = seen_packages[pkg]

        all_to_enrich = [sui_token] + list(seen_packages.values())
        try:
            await enrich_prices(all_to_enrich, session)
        except Exception as exc:
            logger.warning(
                "Price enrichment failed for SUI wallet %s...: %s", address[:20], exc
            )

        sui_price = sui_token.price_usd or 0.0

        # ----------------------------------------------------------------
        # Build asset list — SUI first, then stables, then other tokens
        # ----------------------------------------------------------------
        assets: list[AssetSchema] = []

        if sui_raw > 0:
            sui_amount = sui_raw / _SUI_MIST_PER_SUI
            assets.append(
                AssetSchema(
                    coin="SUI",
                    amount=sui_amount,
                    value_usd=sui_amount * sui_price,
                )
            )

        for sym, raw, usd_price in stable_entries:
            # Determine decimals from _STABLE_COINS
            amount = 0.0
            for ct, (s, dec, p) in _STABLE_COINS.items():
                if s == sym and p == usd_price:
                    amount = raw / (10**dec)
                    break
            if amount >= _DUST_MIN_AMOUNT:
                assets.append(
                    AssetSchema(coin=sym, amount=amount, value_usd=amount * usd_price)
                )

        for sym, raw, coin_type in unknown_entries:
            tp = ct_to_tp.get(coin_type)
            price = (tp.price_usd or 0.0) if tp else 0.0
            amount = raw / _SUI_MIST_PER_SUI
            assets.append(
                AssetSchema(coin=sym, amount=amount, value_usd=amount * price)
            )

        return self._to_service_balance(address, assets)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_service_balance(
        address: str, assets: list[AssetSchema]
    ) -> ServiceBalanceSchema:
        total_usd = sum(a.value_usd for a in assets)
        account = AccountBalanceSchema(
            account_type="spot",
            assets=assets,
            total_usd=total_usd,
        )
        return ServiceBalanceSchema(
            service=SuiService.service_name_for(address),
            accounts=[account] if assets else [],
            assets=assets,
            total_usd=total_usd,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def fetch_wallet_balance(self, address: str) -> ServiceBalanceSchema:
        target = (address or "").strip()
        if not target:
            raise ValueError("SUI wallet address is required")
        if not self.is_sui_address(target):
            raise ValueError(
                f"Address '{target}' is not a recognised SUI address "
                "(expected 0x followed by 64 hex characters)"
            )
        return await self.fetch_sui_balance(target)

    async def _coin_metadata(self, coin_type: str) -> tuple[str, int]:
        if coin_type == _SUI_COIN_TYPE:
            return "SUI", 9
        if coin_type in _STABLE_COINS:
            symbol, decimals, _ = _STABLE_COINS[coin_type]
            return symbol, decimals
        try:
            meta = await self._rpc_call("suix_getCoinMetadata", [coin_type])
            if isinstance(meta, dict):
                return str(meta.get("symbol") or self._extract_symbol(coin_type)), int(
                    meta.get("decimals") or 9
                )
        except Exception as exc:
            logger.debug("SUI coin metadata failed for %s: %s", coin_type, exc)
        return self._extract_symbol(coin_type), 9

    @staticmethod
    def _transaction_status(item: dict[str, Any]) -> str:
        effects = item.get("effects") if isinstance(item.get("effects"), dict) else {}
        status = effects.get("status") if isinstance(effects.get("status"), dict) else {}
        raw = str(status.get("status") or "").lower()
        if raw == "success":
            return "ok"
        if raw == "failure":
            return "failed"
        return "pending"

    async def fetch_wallet_transactions(
        self,
        address: str,
        *,
        service: str | None = None,
        integration_id: int | None = None,
        since: datetime | None = None,
        limit: int = 20,
    ) -> list[TransactionSchema]:
        target = (address or "").strip()
        if not target:
            raise ValueError("SUI wallet address is required")
        if not self.is_sui_address(target):
            raise ValueError(
                f"Address '{target}' is not a recognised SUI address "
                "(expected 0x followed by 64 hex characters)"
            )

        page_limit = max(1, min(int(limit or 20), 50))
        query_options = {
            "showBalanceChanges": True,
            "showEffects": True,
            "showInput": True,
        }
        rows_by_digest: dict[str, dict[str, Any]] = {}
        for filter_key in ("FromAddress", "ToAddress"):
            result = await self._rpc_call(
                "suix_queryTransactionBlocks",
                [
                    {
                        "filter": {filter_key: target},
                        "options": query_options,
                    },
                    None,
                    page_limit,
                    True,
                ],
            )
            for row in (result.get("data") if isinstance(result, dict) else []) or []:
                if isinstance(row, dict) and row.get("digest"):
                    rows_by_digest[str(row["digest"])] = row
        rows = list(rows_by_digest.values())
        service_key = service or self.service_name_for(target)
        target_lower = target.lower()
        transactions: list[TransactionSchema] = []
        metadata_cache: dict[str, tuple[str, int]] = {}

        for item in rows or []:
            if not isinstance(item, dict):
                continue
            digest = str(item.get("digest") or "").strip()
            if not digest:
                continue
            timestamp_ms = item.get("timestampMs")
            tx_timestamp = None
            if timestamp_ms:
                try:
                    tx_timestamp = datetime.fromtimestamp(
                        float(timestamp_ms) / 1000,
                        tz=timezone.utc,
                    )
                except (TypeError, ValueError, OSError):
                    tx_timestamp = None
            if since is not None and tx_timestamp is not None and tx_timestamp < since:
                continue

            changes = item.get("balanceChanges") or []
            for change in changes:
                if not isinstance(change, dict):
                    continue
                owner = change.get("owner") if isinstance(change.get("owner"), dict) else {}
                owner_addr = str(owner.get("AddressOwner") or "").lower()
                if owner_addr != target_lower:
                    continue
                coin_type = str(change.get("coinType") or "")
                if not coin_type:
                    continue
                try:
                    raw_delta = int(change.get("amount") or 0)
                except (TypeError, ValueError):
                    continue
                if raw_delta == 0:
                    continue
                if coin_type not in metadata_cache:
                    metadata_cache[coin_type] = await self._coin_metadata(coin_type)
                symbol, decimals = metadata_cache[coin_type]
                amount = abs(raw_delta) / (10**decimals)
                if amount <= 0:
                    continue
                coin_hash = hashlib.sha1(coin_type.encode("utf-8")).hexdigest()[:12]
                transactions.append(
                    TransactionSchema(
                        integration_id=integration_id,
                        tx_id=f"{service_key}:{digest}:{coin_hash}",
                        service=service_key,
                        tx_type="deposit" if raw_delta > 0 else "withdrawal",
                        currency=symbol,
                        amount=amount,
                        fee=0.0,
                        fee_currency=None,
                        network="sui",
                        address=target,
                        status=self._transaction_status(item),
                        txid=digest,
                        tx_timestamp=tx_timestamp,
                        notified=False,
                    )
                )
        return transactions

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


sui_service = SuiService()
