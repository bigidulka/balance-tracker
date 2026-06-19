"""
TronGrid + tonapi.io wallet balance service.

Covers the two non-EVM chains that the OKX priapi pipeline does not support:
  - TRON  (TronGrid public REST, no API key required)
  - TON   (tonapi.io public REST, no API key required)

USD value strategy
------------------
TRON / TRC20:
    Native TRX and all TRC20 tokens are priced via 3-tier enrichment
    (DexScreener → GeckoTerminal → DEXTools).  Known stablecoins use a
    static $1.00 shortcut to avoid unnecessary API calls.
    CoinGecko is no longer used (was rate-limited at 429).

TON / jettons:
    tonapi.io returns prices for popular jettons.  Native TON price is also
    resolved first via tonapi; if it returns 0, price_enrichment is used as
    a fallback.  Jettons with a zero tonapi price also fall back to
    price_enrichment using the jetton master contract address.
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
from app.services.price_enrichment import TokenPrice, enrich_prices, get_native_price

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Known TRC20 stablecoins — static price, no enrichment needed
# {contract: (symbol, decimals, usd_price)}
# ---------------------------------------------------------------------------
_TRON_STABLES: dict[str, tuple[str, int, float]] = {
    "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": ("USDT", 6, 1.0),
    "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8": ("USDC", 6, 1.0),
    "TXpw8XeWYeTUd4quDskoUqeQPowRh4jY65": ("USDD", 18, 1.0),
}

# Known TRC20 tokens whose price must be resolved dynamically
_TRON_DYNAMIC: dict[str, tuple[str, int]] = {
    # symbol, decimals
    "TNUC9Qb1rRpN8CkknootpR9kTGxh1FdS5z": ("WTRX", 6),
    "TFczxzPhnThNMqeYZZjHTV8Bz5sN61G7bV": ("NFT", 6),
}

_TRONGRID_ACCOUNT_URL = "https://api.trongrid.io/v1/accounts/{address}"
_TRONGRID_TRANSACTIONS_URL = "https://api.trongrid.io/v1/accounts/{address}/transactions"
_TRONGRID_TRC20_TRANSACTIONS_URL = "https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
_TONAPI_ACCOUNT_URL = "https://tonapi.io/v2/accounts/{address}"
_TONAPI_EVENTS_URL = "https://tonapi.io/v2/accounts/{address}/events"
_TONAPI_JETTONS_URL = "https://tonapi.io/v2/accounts/{address}/jettons"
_TONAPI_RATES_URL = "https://tonapi.io/v2/rates"

_TRON_ADDRESS_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
_TON_ADDRESS_RE = re.compile(r"^(EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}$")

# Dust filter — skip tokens below this amount
_DUST_MIN = 1e-9


class TronTonService:
    """Fetch wallet balances for TRON and TON wallets without an API key."""

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
        return {
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    # ------------------------------------------------------------------
    # Address family detection
    # ------------------------------------------------------------------

    @classmethod
    def is_tron_address(cls, address: str) -> bool:
        return bool(_TRON_ADDRESS_RE.fullmatch((address or "").strip()))

    @classmethod
    def is_ton_address(cls, address: str) -> bool:
        return bool(_TON_ADDRESS_RE.fullmatch((address or "").strip()))

    @staticmethod
    def service_name_for(identifier: str) -> str:
        """Return canonical service key for a TRON or TON wallet address.

        TRON address (starts with T) → tron_{addr}
        TON address  (UQ.../EQ...)   → ton_{addr}
        Unknown                      → tron_ton_{addr}  (legacy fallback)
        """
        normalized = (identifier or "").strip().lower()
        raw = (identifier or "").strip()
        if TronTonService.is_tron_address(raw):
            return f"tron_{normalized}"
        if TronTonService.is_ton_address(raw):
            return f"ton_{normalized}"
        # Legacy fallback — should not happen for valid addresses
        return f"tron_ton_{normalized}"

    # ------------------------------------------------------------------
    # Shared helper
    # ------------------------------------------------------------------

    @staticmethod
    def _to_service_balance(
        identifier: str, assets: list[AssetSchema]
    ) -> ServiceBalanceSchema:
        total_usd = sum(a.value_usd for a in assets)
        account = AccountBalanceSchema(
            account_type="spot",
            assets=assets,
            total_usd=total_usd,
        )
        return ServiceBalanceSchema(
            service=TronTonService.service_name_for(identifier),
            accounts=[account] if assets else [],
            assets=assets,
            total_usd=total_usd,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

    # ------------------------------------------------------------------
    # TRON
    # ------------------------------------------------------------------

    async def fetch_tron_balance(self, address: str) -> ServiceBalanceSchema:
        """
        Fetch TRON wallet balance via TronGrid.

        Pricing tiers (via price_enrichment):
          1. DexScreener — batch, fast
          2. GeckoTerminal — batch, rate-limited
          3. DEXTools — per-address, last resort
        Stablecoins (USDT, USDC, USDD) use static $1.00 — no API needed.
        """
        session = await self._get_session()

        url = _TRONGRID_ACCOUNT_URL.format(address=address)
        try:
            async with session.get(
                url,
                headers=self._default_headers(),
                **request_proxy_kwargs(),
            ) as resp:
                resp.raise_for_status()
                data: dict[str, Any] = await resp.json()
        except Exception as exc:
            raise ValueError(f"TronGrid request failed for {address}: {exc}") from exc

        account_list: list[dict[str, Any]] = data.get("data") or []
        if not account_list:
            return self._to_service_balance(address, [])

        account = account_list[0]

        # ----------------------------------------------------------------
        # Bucket raw balances
        # ----------------------------------------------------------------
        # (contract, symbol, decimals) for tokens needing price lookup
        to_enrich: list[tuple[str, str, int]] = []
        # (symbol, amount, usd_price) for stables — ready immediately
        stable_assets: list[tuple[str, float, float]] = []

        # Native TRX
        balance_sun: int = int(account.get("balance") or 0)
        trx_amount = balance_sun / 1_000_000 if balance_sun > 0 else 0.0

        # TRC20 tokens
        trc20_pending: list[tuple[str, str, int]] = []  # (contract, symbol, decimals)
        trc20_amounts: dict[str, float] = {}  # contract -> amount

        trc20_list: list[dict[str, str]] = account.get("trc20") or []
        for token_dict in trc20_list:
            for contract, raw_str in token_dict.items():
                # Stablecoin shortcut
                if contract in _TRON_STABLES:
                    sym, dec, usd_price = _TRON_STABLES[contract]
                    try:
                        amount = float(raw_str) / (10**dec)
                    except (ValueError, TypeError):
                        continue
                    if amount >= _DUST_MIN:
                        stable_assets.append((sym, amount, usd_price))
                    continue

                # Known dynamic token
                if contract in _TRON_DYNAMIC:
                    sym, dec = _TRON_DYNAMIC[contract]
                else:
                    sym = contract[:8] + "..."
                    dec = 6  # assume 6 decimals for unknown TRC20

                try:
                    amount = float(raw_str) / (10**dec)
                except (ValueError, TypeError):
                    continue
                if amount < _DUST_MIN:
                    continue

                trc20_amounts[contract] = amount
                trc20_pending.append((contract, sym, dec))

        # ----------------------------------------------------------------
        # Price resolution
        # ----------------------------------------------------------------
        # TRX is a native coin — no contract address, resolved via Binance.
        # TRC20 tokens — 3-tier enrichment (DS → GT → DEXTools).
        # Both run concurrently.

        # Deduplicate TRC20 contracts before sending
        seen: dict[str, TokenPrice] = {}
        contract_to_tp: dict[str, TokenPrice] = {}
        for contract, sym, dec in trc20_pending:
            if contract not in seen:
                tp = TokenPrice(address=contract, chain="tron", extra={"symbol": sym})
                seen[contract] = tp
            contract_to_tp[contract] = seen[contract]

        trx_price_box: list[float] = [0.0]

        async def _fetch_trx() -> None:
            trx_price_box[0] = await get_native_price("TRX", session)

        try:
            await asyncio.gather(
                _fetch_trx(),
                enrich_prices(list(seen.values()), session)
                if seen
                else asyncio.sleep(0),
            )
        except Exception as exc:
            logger.warning("Price fetch failed for TRON %s...: %s", address[:12], exc)

        trx_price = trx_price_box[0]

        # ----------------------------------------------------------------
        # Assemble final asset list
        # ----------------------------------------------------------------
        assets: list[AssetSchema] = []

        if trx_amount >= _DUST_MIN:
            assets.append(
                AssetSchema(
                    coin="TRX",
                    amount=trx_amount,
                    value_usd=trx_amount * trx_price,
                )
            )

        for sym, amount, usd_price in stable_assets:
            assets.append(
                AssetSchema(coin=sym, amount=amount, value_usd=amount * usd_price)
            )

        for contract, sym, dec in trc20_pending:
            amount = trc20_amounts.get(contract, 0.0)
            tp = contract_to_tp.get(contract)
            price = (tp.price_usd or 0.0) if tp else 0.0
            assets.append(
                AssetSchema(coin=sym, amount=amount, value_usd=amount * price)
            )

        return self._to_service_balance(address, assets)

    # ------------------------------------------------------------------
    # TON
    # ------------------------------------------------------------------

    @staticmethod
    async def _get_ton_native_price(session: aiohttp.ClientSession) -> float:
        """
        TON/USD price: Binance primary, tonapi.io rates as fallback.
        Both are fast; Binance is more reliable at high frequency.
        """
        price = await get_native_price("TON", session)
        if price > 0:
            return price
        # Fallback: tonapi rates
        try:
            async with session.get(
                _TONAPI_RATES_URL,
                params={"tokens": "TON", "currencies": "USD"},
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0",
                },
                **request_proxy_kwargs(),
            ) as resp:
                resp.raise_for_status()
                data: dict[str, Any] = await resp.json()
                return float(
                    data.get("rates", {}).get("TON", {}).get("prices", {}).get("USD", 0)
                    or 0
                )
        except Exception as exc:
            logger.debug("tonapi rates fallback failed: %s", exc)
            return 0.0

    async def fetch_ton_balance(self, address: str) -> ServiceBalanceSchema:
        """
        Fetch TON wallet balance via tonapi.io.

        Native TON price: tonapi rates endpoint, with price_enrichment as fallback.
        Jetton prices: tonapi embedded prices; price_enrichment used as fallback
        for any jetton that tonapi returns with $0 price.
        """
        session = await self._get_session()
        assets: list[AssetSchema] = []

        # --- Native TON account ---
        acc_url = _TONAPI_ACCOUNT_URL.format(address=address)
        try:
            async with session.get(
                acc_url,
                headers=self._default_headers(),
                **request_proxy_kwargs(),
            ) as resp:
                resp.raise_for_status()
                acc_data: dict[str, Any] = await resp.json()
        except Exception as exc:
            raise ValueError(
                f"tonapi account request failed for {address}: {exc}"
            ) from exc

        balance_nanoton: int = int(acc_data.get("balance") or 0)
        ton_amount = balance_nanoton / 1_000_000_000 if balance_nanoton > 0 else 0.0

        # --- Jettons ---
        jet_url = _TONAPI_JETTONS_URL.format(address=address)
        try:
            async with session.get(
                jet_url,
                params={"currencies": "usd"},
                headers=self._default_headers(),
                **request_proxy_kwargs(),
            ) as resp:
                resp.raise_for_status()
                jet_data: dict[str, Any] = await resp.json()
        except Exception as exc:
            logger.debug("tonapi jettons request failed for %s: %s", address, exc)
            jet_data = {}

        # ----------------------------------------------------------------
        # Parse jettons — collect those with zero tonapi price for fallback
        # ----------------------------------------------------------------
        # (symbol, amount, tonapi_price, master_contract)
        jetton_rows: list[tuple[str, float, float, str]] = []

        for jetton in jet_data.get("balances") or []:
            jetton_meta: dict[str, Any] = jetton.get("jetton") or {}
            symbol: str = jetton_meta.get("symbol") or "UNKNOWN"
            decimals: int = int(jetton_meta.get("decimals") or 9)
            master: str = (jetton_meta.get("address") or "").strip()

            try:
                raw_balance = int(jetton.get("balance") or 0)
            except (ValueError, TypeError):
                continue

            amount = raw_balance / (10**decimals)
            if amount < _DUST_MIN:
                continue

            price_data = jetton.get("price") or {}
            usd_price: float = float(
                (price_data.get("prices") or {}).get("USD", 0) or 0
            )

            jetton_rows.append((symbol, amount, usd_price, master))

        # ----------------------------------------------------------------
        # Price resolution: TON native + zero-priced jettons concurrently
        # ----------------------------------------------------------------
        # Jettons with no tonapi price — need fallback
        zero_jettons = [
            (sym, amt, master)
            for sym, amt, price, master in jetton_rows
            if price == 0.0 and master
        ]
        seen_masters: dict[str, TokenPrice] = {}
        master_to_tp: dict[str, TokenPrice] = {}
        for sym, amt, master in zero_jettons:
            if master not in seen_masters:
                tp = TokenPrice(address=master, chain="ton", extra={"symbol": sym})
                seen_masters[master] = tp
            master_to_tp[master] = seen_masters[master]

        # Run TON native price + jetton enrichment concurrently
        enrich_batch: list[TokenPrice] = list(seen_masters.values())

        ton_price_box: list[float] = [0.0]

        async def _fetch_ton_price() -> None:
            ton_price_box[0] = await self._get_ton_native_price(session)

        try:
            await asyncio.gather(
                _fetch_ton_price(),
                enrich_prices(enrich_batch, session)
                if enrich_batch
                else asyncio.sleep(0),
            )
        except Exception as exc:
            logger.warning("Price fetch failed for TON %s...: %s", address[:16], exc)

        ton_price = ton_price_box[0]

        # ----------------------------------------------------------------
        # Assemble assets
        # ----------------------------------------------------------------
        if ton_amount >= _DUST_MIN:
            assets.append(
                AssetSchema(
                    coin="TON",
                    amount=ton_amount,
                    value_usd=ton_amount * ton_price,
                )
            )

        for symbol, amount, tonapi_price, master in jetton_rows:
            if tonapi_price > 0.0:
                usd_price = tonapi_price
            else:
                tp = master_to_tp.get(master)
                usd_price = (tp.price_usd or 0.0) if tp else 0.0
            assets.append(
                AssetSchema(
                    coin=symbol,
                    amount=amount,
                    value_usd=amount * usd_price,
                )
            )

        return self._to_service_balance(address, assets)

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    @staticmethod
    def _trx_address_to_hex(address: str) -> str:
        alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        value = 0
        for char in address:
            value = value * 58 + alphabet.index(char)
        raw = value.to_bytes(25, "big")
        payload, checksum = raw[:-4], raw[-4:]
        expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        if checksum != expected:
            raise ValueError("invalid TRON address checksum")
        return payload.hex()

    @staticmethod
    def _ts_from_millis(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    @staticmethod
    def _ton_account_address(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("address") or "")
        return str(value or "")

    async def fetch_tron_transactions(
        self,
        address: str,
        *,
        service: str | None = None,
        integration_id: int | None = None,
        since: datetime | None = None,
        limit: int = 20,
    ) -> list[TransactionSchema]:
        target = (address or "").strip()
        if not self.is_tron_address(target):
            raise ValueError(f"Address '{target}' is not a recognised TRON address")
        service_key = service or self.service_name_for(target)
        page_limit = max(1, min(int(limit or 20), 50))
        session = await self._get_session()
        target_hex = self._trx_address_to_hex(target).lower()
        transactions: list[TransactionSchema] = []

        native_url = _TRONGRID_TRANSACTIONS_URL.format(address=target)
        try:
            async with session.get(
                native_url,
                params={
                    "only_confirmed": "true",
                    "limit": page_limit,
                    "order_by": "block_timestamp,desc",
                },
                headers=self._default_headers(),
                **request_proxy_kwargs(),
            ) as resp:
                resp.raise_for_status()
                native_data: dict[str, Any] = await resp.json()
        except Exception as exc:
            raise ValueError(f"TronGrid transactions request failed for {target}: {exc}") from exc

        for item in native_data.get("data") or []:
            if not isinstance(item, dict):
                continue
            txid = str(item.get("txID") or "").strip()
            contract = ((item.get("raw_data") or {}).get("contract") or [{}])[0]
            if contract.get("type") != "TransferContract":
                continue
            value = ((contract.get("parameter") or {}).get("value") or {})
            owner_hex = str(value.get("owner_address") or "").lower()
            to_hex = str(value.get("to_address") or "").lower()
            if not txid or target_hex not in {owner_hex, to_hex}:
                continue
            try:
                amount = abs(float(value.get("amount") or 0.0)) / 1_000_000
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            tx_timestamp = self._ts_from_millis(item.get("block_timestamp"))
            if since is not None and tx_timestamp is not None and tx_timestamp < since:
                continue
            ret = (item.get("ret") or [{}])[0]
            status = "ok" if str(ret.get("contractRet") or "").upper() == "SUCCESS" else "failed"
            transactions.append(
                TransactionSchema(
                    integration_id=integration_id,
                    tx_id=f"{service_key}:trx:{txid}",
                    service=service_key,
                    tx_type="deposit" if to_hex == target_hex else "withdrawal",
                    currency="TRX",
                    amount=amount,
                    fee=0.0,
                    fee_currency=None,
                    network="tron",
                    address=target,
                    address_from=owner_hex or None,
                    address_to=to_hex or None,
                    status=status,
                    txid=txid,
                    tx_timestamp=tx_timestamp,
                    notified=False,
                )
            )

        trc20_url = _TRONGRID_TRC20_TRANSACTIONS_URL.format(address=target)
        try:
            async with session.get(
                trc20_url,
                params={
                    "only_confirmed": "true",
                    "limit": page_limit,
                    "order_by": "block_timestamp,desc",
                },
                headers=self._default_headers(),
                **request_proxy_kwargs(),
            ) as resp:
                resp.raise_for_status()
                trc20_data: dict[str, Any] = await resp.json()
        except Exception as exc:
            logger.debug("TronGrid TRC20 transactions request failed for %s: %s", target, exc)
            trc20_data = {}

        for item in trc20_data.get("data") or []:
            if not isinstance(item, dict):
                continue
            txid = str(item.get("transaction_id") or "").strip()
            from_addr = str(item.get("from") or "")
            to_addr = str(item.get("to") or "")
            token_info = item.get("token_info") if isinstance(item.get("token_info"), dict) else {}
            contract = str(token_info.get("address") or item.get("token_address") or "")
            decimals = int(token_info.get("decimals") or 6)
            symbol = str(token_info.get("symbol") or "TRC20")
            try:
                amount = abs(float(item.get("value") or 0.0)) / (10**decimals)
            except (TypeError, ValueError):
                continue
            if not txid or amount <= 0:
                continue
            tx_timestamp = self._ts_from_millis(item.get("block_timestamp"))
            if since is not None and tx_timestamp is not None and tx_timestamp < since:
                continue
            tx_type = "deposit" if to_addr.lower() == target.lower() else "withdrawal"
            transactions.append(
                TransactionSchema(
                    integration_id=integration_id,
                    tx_id=f"{service_key}:trc20:{txid}:{contract}",
                    service=service_key,
                    tx_type=tx_type,
                    currency=symbol,
                    amount=amount,
                    fee=0.0,
                    fee_currency=None,
                    network="tron",
                    address=target,
                    address_from=from_addr or None,
                    address_to=to_addr or None,
                    status="ok",
                    txid=txid,
                    tx_timestamp=tx_timestamp,
                    notified=False,
                )
            )
        return transactions

    async def fetch_ton_transactions(
        self,
        address: str,
        *,
        service: str | None = None,
        integration_id: int | None = None,
        since: datetime | None = None,
        limit: int = 20,
    ) -> list[TransactionSchema]:
        target = (address or "").strip()
        if not self.is_ton_address(target):
            raise ValueError(f"Address '{target}' is not a recognised TON address")
        service_key = service or self.service_name_for(target)
        page_limit = max(1, min(int(limit or 20), 50))
        session = await self._get_session()
        url = _TONAPI_EVENTS_URL.format(address=target)
        try:
            async with session.get(
                url,
                params={"limit": page_limit},
                headers=self._default_headers(),
                **request_proxy_kwargs(),
            ) as resp:
                resp.raise_for_status()
                data: dict[str, Any] = await resp.json()
        except Exception as exc:
            raise ValueError(f"tonapi events request failed for {target}: {exc}") from exc

        transactions: list[TransactionSchema] = []
        target_lower = target.lower()
        for event in data.get("events") or []:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id") or "").strip()
            timestamp = event.get("timestamp")
            tx_timestamp = None
            if timestamp:
                try:
                    tx_timestamp = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
                except (TypeError, ValueError, OSError):
                    tx_timestamp = None
            if since is not None and tx_timestamp is not None and tx_timestamp < since:
                continue
            status = "pending" if bool(event.get("in_progress")) else "ok"
            for index, action in enumerate(event.get("actions") or []):
                if not isinstance(action, dict):
                    continue
                action_type = str(action.get("type") or "")
                payload = action.get(action_type) if isinstance(action.get(action_type), dict) else {}
                if action_type == "TonTransfer":
                    sender = self._ton_account_address(payload.get("sender"))
                    recipient = self._ton_account_address(payload.get("recipient"))
                    try:
                        amount = abs(float(payload.get("amount") or 0.0)) / 1_000_000_000
                    except (TypeError, ValueError):
                        continue
                    symbol = "TON"
                elif action_type == "JettonTransfer":
                    sender = self._ton_account_address(payload.get("sender"))
                    recipient = self._ton_account_address(payload.get("recipient"))
                    jetton = payload.get("jetton") if isinstance(payload.get("jetton"), dict) else {}
                    symbol = str(jetton.get("symbol") or "JETTON")
                    decimals = int(jetton.get("decimals") or 9)
                    try:
                        amount = abs(float(payload.get("amount") or 0.0)) / (10**decimals)
                    except (TypeError, ValueError):
                        continue
                else:
                    continue
                if amount <= 0:
                    continue
                tx_type = "deposit" if recipient.lower() == target_lower else "withdrawal"
                transactions.append(
                    TransactionSchema(
                        integration_id=integration_id,
                        tx_id=f"{service_key}:ton:{event_id}:{index}",
                        service=service_key,
                        tx_type=tx_type,
                        currency=symbol,
                        amount=amount,
                        fee=0.0,
                        fee_currency=None,
                        network="ton",
                        address=target,
                        address_from=sender or None,
                        address_to=recipient or None,
                        status=status,
                        txid=event_id,
                        tx_timestamp=tx_timestamp,
                        notified=False,
                    )
                )
        return transactions

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
        if self.is_tron_address(target):
            return await self.fetch_tron_transactions(
                target,
                service=service,
                integration_id=integration_id,
                since=since,
                limit=limit,
            )
        if self.is_ton_address(target):
            return await self.fetch_ton_transactions(
                target,
                service=service,
                integration_id=integration_id,
                since=since,
                limit=limit,
            )
        raise ValueError(f"Address '{target}' is not a recognised TRON or TON address")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def fetch_wallet_balance(self, address: str) -> ServiceBalanceSchema:
        target = (address or "").strip()
        if not target:
            raise ValueError("wallet address is required")

        if self.is_tron_address(target):
            return await self.fetch_tron_balance(target)
        if self.is_ton_address(target):
            return await self.fetch_ton_balance(target)
        raise ValueError(f"Address '{target}' is not a recognised TRON or TON address")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


tron_ton_service = TronTonService()
