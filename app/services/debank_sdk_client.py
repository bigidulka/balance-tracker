import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from app.core.config import get_settings
from app.core.http import request_proxy_kwargs, session_kwargs
from app.schemas.balance import AssetSchema, AccountBalanceSchema, ServiceBalanceSchema, TransactionSchema
from app.services.balance_integrity import validate_balance_shape

logger = logging.getLogger(__name__)
settings = get_settings()


class DeBankSdkClient:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=settings.debank_sdk_timeout_seconds)
                # debank-sdk is internal docker service — no proxy
                self._session = aiohttp.ClientSession(
                    **session_kwargs(timeout, target_url=settings.debank_sdk_base_url)
                )
        return self._session

    async def _request_json(self, path: str) -> dict[str, Any]:
        url = f"{settings.debank_sdk_base_url}{path}"
        session = await self._get_session()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with session.get(
                    url,
                    **request_proxy_kwargs(target_url=url),
                ) as response:
                    payload = await response.json()
                    if response.status >= 400:
                        err_msg = payload.get("error", "") if isinstance(payload, dict) else ""
                        last_error = RuntimeError(f"DeBank SDK {response.status}: {err_msg or response.status}")
                        if response.status in (429, 502, 503):
                            logger.warning(
                                "DeBank SDK rate-limit/server error %d for %s, retry %d/3",
                                response.status, path, attempt + 1,
                            )
                            await asyncio.sleep(1.0 * (attempt + 1))
                            continue
                        raise last_error
                    if not isinstance(payload, dict):
                        raise RuntimeError("DeBank SDK returned unexpected response shape")
                    return payload
            except aiohttp.ClientError as exc:
                last_error = exc
                logger.warning("DeBank SDK connection error for %s, retry %d/3: %s", path, attempt + 1, exc)
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
        raise last_error or RuntimeError("DeBank SDK request failed after retries")

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
        balance = ServiceBalanceSchema(
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
        validate_balance_shape(balance)
        return balance

    @staticmethod
    def _dominant_transfer(transfers: list[dict[str, Any]]) -> dict[str, Any]:
        if not transfers:
            return {}
        return max(
            transfers,
            key=lambda item: abs(float(item.get("usdValue") or 0.0)),
        )

    @staticmethod
    def _transaction_status(raw_status: Any) -> str:
        try:
            status = int(raw_status)
        except (TypeError, ValueError):
            return "pending"
        if status == 1:
            return "ok"
        if status < 0:
            return "failed"
        return "pending"

    def _build_transaction(
        self,
        *,
        item: dict[str, Any],
        wallet_address: str,
        service: str,
        integration_id: int | None,
    ) -> TransactionSchema | None:
        key = str(item.get("key") or "").strip()
        if not key:
            return None
        receives = [entry for entry in (item.get("receives") or []) if isinstance(entry, dict)]
        sends = [entry for entry in (item.get("sends") or []) if isinstance(entry, dict)]
        received_usd = float(item.get("receivedUsd") or 0.0)
        sent_usd = float(item.get("sentUsd") or 0.0)
        tx_type = "deposit" if received_usd >= sent_usd else "withdrawal"
        transfer = self._dominant_transfer(receives if tx_type == "deposit" else sends)
        if not transfer:
            transfer = self._dominant_transfer(sends if tx_type == "deposit" else receives)
        symbol = str(transfer.get("symbol") or transfer.get("name") or "UNKNOWN")
        amount = abs(float(transfer.get("amount") or 0.0))
        timestamp = item.get("timeAt")
        tx_timestamp = None
        if timestamp:
            try:
                tx_timestamp = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                tx_timestamp = None
        other_addr = str(item.get("otherAddr") or "") or None
        address_from = other_addr if tx_type == "deposit" else wallet_address
        address_to = wallet_address if tx_type == "deposit" else other_addr
        chain = str(item.get("chain") or item.get("chainName") or "dex")
        return TransactionSchema(
            integration_id=integration_id,
            tx_id=f"{service}:{key}",
            service=service,
            tx_type=tx_type,
            currency=symbol,
            amount=amount,
            fee=0.0,
            fee_currency=None,
            network=chain,
            address=wallet_address,
            address_from=address_from,
            address_to=address_to,
            status=self._transaction_status(item.get("status")),
            txid=key,
            tx_timestamp=tx_timestamp,
            notified=False,
        )

    async def fetch_wallet_transactions(
        self,
        wallet_address: str,
        *,
        service: str,
        integration_id: int | None = None,
        since: datetime | None = None,
        limit: int = 20,
    ) -> list[TransactionSchema]:
        page_size = max(1, min(int(limit or 20), 20))
        path = (
            f"/users/0/wallets/{quote(wallet_address, safe='')}/transactions"
            f"?cursor=0&pageSize={page_size}&hideScam=true"
        )
        payload = await self._request_json(path)
        items = payload.get("items") or []
        transactions: list[TransactionSchema] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            tx = self._build_transaction(
                item=item,
                wallet_address=wallet_address,
                service=service,
                integration_id=integration_id,
            )
            if tx is None:
                continue
            if since is not None and tx.tx_timestamp is not None and tx.tx_timestamp < since:
                continue
            transactions.append(tx)
        return transactions

    async def healthcheck(self) -> dict[str, Any]:
        return await self._request_json("/health")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


debank_sdk_client = DeBankSdkClient()