import asyncio
import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlencode

import aiohttp

from app.core.config import Settings
from app.core.http import build_proxy_url, is_http_proxy, session_kwargs
from app.schemas.balance import AccountBalanceSchema, AssetSchema, ServiceBalanceSchema

if TYPE_CHECKING:
    from app.services.ccxt_manager import CCXTManager


logger = logging.getLogger(__name__)


_PROXY_RETRYABLE_ERRORS = (
    aiohttp.ClientProxyConnectionError,
    aiohttp.ClientHttpProxyError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ServerConnectionError,
    asyncio.TimeoutError,
    TimeoutError,
)


class BalanceGateway(Protocol):
    gateway_name: str

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema: ...


def _iso_utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _safe_float(source: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = source.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _merge_accounts(
    service: str,
    accounts_payload: list[dict[str, Any]],
    calculate_usd_value,
) -> ServiceBalanceSchema:
    aggregated_accounts: list[AccountBalanceSchema] = []
    flat_assets: dict[str, AssetSchema] = {}
    total_usd = 0.0
    warnings: list[str] = []
    mirror_seen_account_types: set[str] = set()

    for account in accounts_payload:
        account_type = account["account_type"]
        tickers = account.get("tickers", {})
        assets: list[AssetSchema] = []
        account_total = 0.0
        mirror_of = account.get("mirror_of")
        account_error = account.get("error")

        for raw_asset in account.get("assets", []):
            coin = str(raw_asset.get("coin", "")).upper()
            amount = float(raw_asset.get("amount", 0) or 0)
            if not coin or amount <= 0:
                continue

            explicit_value = raw_asset.get("value_usd")
            value_usd = (
                float(explicit_value or 0)
                if explicit_value is not None
                else float(calculate_usd_value(coin, amount, tickers))
            )

            asset = AssetSchema(coin=coin, amount=amount, value_usd=value_usd)
            if amount > 0 and value_usd <= 0:
                warnings.append(f"unvalued_asset:{account_type}:{coin}")
            assets.append(asset)
            account_total += value_usd

            # For mirrored accounts: don't add to flat_assets or total_usd
            # (they were already counted in the source account)
            if not mirror_of:
                existing = flat_assets.get(coin)
                if existing is None:
                    flat_assets[coin] = asset
                else:
                    flat_assets[coin] = AssetSchema(
                        coin=coin,
                        amount=existing.amount + asset.amount,
                        value_usd=existing.value_usd + asset.value_usd,
                    )

        if assets or account_error is not None:
            aggregated_accounts.append(
                AccountBalanceSchema(
                    account_type=account_type,
                    assets=assets,
                    total_usd=account_total,
                    mirror_of=mirror_of,
                    error=str(account_error) if account_error else None,
                )
            )
            # Only add to total_usd if this is NOT a mirrored account
            if not mirror_of:
                total_usd += account_total


    return ServiceBalanceSchema(
        service=service,
        accounts=aggregated_accounts,
        assets=list(flat_assets.values()),
        total_usd=total_usd,
        updated_at=datetime.now(timezone.utc),
        actual=bool(aggregated_accounts or flat_assets or total_usd > 0)
        and not any(account.error for account in aggregated_accounts),
        warnings=sorted(set(warnings)),
    )


class BaseRestGateway:
    gateway_name = "rest"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._direct_session: aiohttp.ClientSession | None = None
        self._proxy_session: aiohttp.ClientSession | None = None

    def _resolve_config(
        self,
        exchange_id: str,
        config_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return config_override or self.settings.get_exchange_config(exchange_id)

    def _timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=max(5, self.settings.request_timeout))

    def _proxy_timeout(self) -> aiohttp.ClientTimeout:
        """Short timeout for proxy attempt so signed timestamps stay valid on
        direct retry.  Exchange APIs typically reject timestamps older than
        5 seconds (``recvWindow``), so the proxy probe must be very fast."""
        return aiohttp.ClientTimeout(total=2)

    async def _decode_json_response(self, response: aiohttp.ClientResponse) -> Any:
        try:
            return await response.json()
        except aiohttp.ContentTypeError:
            text = await response.text()
            return json.loads(text)

    async def _get_direct_session(self) -> aiohttp.ClientSession:
        if self._direct_session is None or self._direct_session.closed:
            self._direct_session = aiohttp.ClientSession(**session_kwargs(self._timeout()))
        return self._direct_session

    async def _get_proxy_session(self) -> aiohttp.ClientSession:
        if self._proxy_session is None or self._proxy_session.closed:
            self._proxy_session = aiohttp.ClientSession(**session_kwargs(self._proxy_timeout()))
        return self._proxy_session

    async def close(self) -> None:
        for session in (self._direct_session, self._proxy_session):
            if session and not session.closed:
                await session.close()
        self._direct_session = None
        self._proxy_session = None

    async def _request_with_optional_proxy(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: Any = None,
    ) -> Any:
        session = await self._get_direct_session()
        async with session.request(
            method,
            url,
            params=params,
            headers=headers,
            data=data,
        ) as response:
            response.raise_for_status()
            return await self._decode_json_response(response)

    async def _json_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: Any = None,
    ) -> Any:
        proxy_url = build_proxy_url()
        if not proxy_url or not is_http_proxy(proxy_url):
            return await self._request_with_optional_proxy(
                method,
                url,
                params=params,
                headers=headers,
                data=data,
            )

        try:
            session = await self._get_proxy_session()
            async with session.request(
                method,
                url,
                params=params,
                headers=headers,
                data=data,
                proxy=proxy_url,
            ) as response:
                response.raise_for_status()
                return await self._decode_json_response(response)
        except _PROXY_RETRYABLE_ERRORS as exc:
            logger.warning(
                "REST proxy request failed for %s %s, retrying direct: %s",
                method,
                url,
                exc,
            )
            return await self._request_with_optional_proxy(
                method,
                url,
                params=params,
                headers=headers,
                data=data,
            )


class CCXTBalanceGateway:
    gateway_name = "ccxt"

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        return await manager._fetch_balance_via_ccxt(
            exchange_id,
            config_override=config_override,
        )


class BinanceRestBalanceGateway(BaseRestGateway):
    def _signed_params(self, params: dict[str, Any], secret: str) -> dict[str, Any]:
        signed = dict(params)
        signed["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        query = urlencode(signed, doseq=True)
        signed["signature"] = hmac.new(
            secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signed

    async def _request(
        self,
        path: str,
        api_key: str,
        secret: str,
        *,
        method: str = "GET",
        base_url: str = "https://api.binance.com",
        params: dict[str, Any] | None = None,
    ) -> Any:
        signed_params = self._signed_params(params or {"recvWindow": 5000}, secret)
        headers = {"X-MBX-APIKEY": api_key}
        return await self._json_request(
            method,
            f"{base_url}{path}",
            params=signed_params,
            headers=headers,
        )

    async def _optional_request(
        self,
        path: str,
        api_key: str,
        secret: str,
        *,
        default: Any,
        method: str = "GET",
        base_url: str = "https://api.binance.com",
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            return await self._request(
                path,
                api_key,
                secret,
                method=method,
                base_url=base_url,
                params=params,
            )
        except aiohttp.ClientResponseError as exc:
            if exc.status in {400, 401, 403, 404}:
                logger.warning(
                    "Binance REST account endpoint %s returned %s, skipping optional account",
                    path,
                    exc.status,
                )
                return default
            raise

    async def _fetch_tickers(self) -> dict[str, dict[str, Any]]:
        rows = await self._json_request(
            "GET", "https://api.binance.com/api/v3/ticker/price"
        )
        tickers: dict[str, dict[str, Any]] = {}
        for row in rows:
            symbol = str(row.get("symbol", ""))
            price = _safe_float(row, "price")
            if symbol.endswith("USDT"):
                tickers[f"{symbol[:-4]}/USDT"] = {"last": price}
            elif symbol.endswith("USDC"):
                tickers[f"{symbol[:-4]}/USDC"] = {"last": price}
            elif symbol.endswith("BUSD"):
                tickers[f"{symbol[:-4]}/BUSD"] = {"last": price}
        return tickers

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        if not api_key or not secret:
            raise ValueError(f"No API key configured for {exchange_id}")

        tickers = await self._fetch_tickers()
        spot = await self._request("/api/v3/account", api_key, secret)
        margin = await self._optional_request(
            "/sapi/v1/margin/account",
            api_key,
            secret,
            default={},
        )
        funding = await self._optional_request(
            "/sapi/v1/asset/get-funding-asset",
            api_key,
            secret,
            default=[],
            method="POST",
            params={"needBtcValuation": "false", "recvWindow": 5000},
        )
        futures = await self._optional_request(
            "/fapi/v3/account",
            api_key,
            secret,
            default={},
            base_url="https://fapi.binance.com",
        )
        delivery = await self._optional_request(
            "/dapi/v1/balance",
            api_key,
            secret,
            default=[],
            base_url="https://dapi.binance.com",
        )

        payload = [
            {
                "account_type": "spot",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item["asset"],
                        "amount": _safe_float(item, "free")
                        + _safe_float(item, "locked"),
                    }
                    for item in spot.get("balances", [])
                ],
            },
            {
                "account_type": "margin",
                "tickers": tickers,
                "assets": [
                    {"coin": item["asset"], "amount": _safe_float(item, "netAsset")}
                    for item in margin.get("userAssets", [])
                ],
            },
            {
                "account_type": "funding",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item["asset"],
                        "amount": _safe_float(item, "free")
                        + _safe_float(item, "locked"),
                    }
                    for item in funding
                ],
            },
            {
                "account_type": "usdt_futures",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item["asset"],
                        "amount": _safe_float(item, "walletBalance"),
                    }
                    for item in futures.get("assets", [])
                ],
            },
            {
                "account_type": "coin_futures",
                "tickers": tickers,
                "assets": [
                    {"coin": item["asset"], "amount": _safe_float(item, "balance")}
                    for item in delivery
                ],
            },
        ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class OkxRestBalanceGateway(BaseRestGateway):
    def _signature(
        self, timestamp: str, method: str, request_path: str, body: str, secret: str
    ) -> str:
        payload = f"{timestamp}{method.upper()}{request_path}{body}"
        digest = hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    async def _request(
        self,
        path: str,
        api_key: str,
        secret: str,
        passphrase: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        query = f"?{urlencode(params, doseq=True)}" if params else ""
        request_path = f"{path}{query}"
        timestamp = _iso_utc_now()
        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": self._signature(
                timestamp, "GET", request_path, "", secret
            ),
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
        }
        payload = await self._json_request(
            "GET",
            f"https://www.okx.com{request_path}",
            headers=headers,
        )
        if payload.get("code") not in {None, "0", 0}:
            raise RuntimeError(f"OKX API error: {payload}")
        return payload.get("data", [])

    async def _fetch_tickers(self) -> dict[str, dict[str, Any]]:
        tickers: dict[str, dict[str, Any]] = {}
        for inst_type in ("SPOT", "SWAP"):
            payload = await self._json_request(
                "GET",
                "https://www.okx.com/api/v5/market/tickers",
                params={"instType": inst_type},
            )
            for item in payload.get("data", []):
                inst_id = str(item.get("instId", ""))
                if "-" in inst_id:
                    tickers[inst_id.replace("-", "/")] = {
                        "last": _safe_float(item, "last")
                    }
        return tickers

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        passphrase = config.get("password", "")
        if not api_key or not secret or not passphrase:
            raise ValueError("OKX API key/secret/password is required for REST mode")

        tickers = await self._fetch_tickers()
        trading = await self._request(
            "/api/v5/account/balance",
            api_key,
            secret,
            passphrase,
        )
        funding = await self._request(
            "/api/v5/asset/balances",
            api_key,
            secret,
            passphrase,
        )

        payload = [
            {
                "account_type": "trading",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item["ccy"],
                        "amount": _safe_float(item, "cashBal"),
                        "value_usd": _safe_float(item, "eqUsd"),
                    }
                    for block in trading
                    for item in block.get("details", [])
                ],
            },
            {
                "account_type": "funding",
                "tickers": tickers,
                "assets": [
                    {"coin": item["ccy"], "amount": _safe_float(item, "bal")}
                    for item in funding
                ],
            },
        ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class BybitRestBalanceGateway(BaseRestGateway):
    recv_window = 5000

    def _sign(
        self, timestamp: str, api_key: str, secret: str, query_string: str
    ) -> str:
        payload = f"{timestamp}{api_key}{self.recv_window}{query_string}"
        return hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        if not api_key or not secret:
            raise ValueError("Bybit API key/secret is required for REST mode")

        params = {"accountType": "UNIFIED"}
        query_string = urlencode(params)
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(self.recv_window),
            "X-BAPI-SIGN": self._sign(timestamp, api_key, secret, query_string),
        }
        payload = await self._json_request(
            "GET",
            "https://api.bybit.com/v5/account/wallet-balance",
            params=params,
            headers=headers,
        )
        if payload.get("retCode") not in {0, "0", None}:
            raise RuntimeError(f"Bybit API error: {payload}")

        accounts = payload.get("result", {}).get("list", [])
        merged_payload = [
            {
                "account_type": "unified",
                "assets": [
                    {
                        "coin": item["coin"],
                        "amount": _safe_float(item, "walletBalance", "equity"),
                        "value_usd": _safe_float(item, "usdValue"),
                    }
                    for account in accounts
                    for item in account.get("coin", [])
                ],
            }
        ]
        return _merge_accounts(
            exchange_id, merged_payload, manager.calculate_usd_value_sync
        )


class BitgetRestBalanceGateway(BaseRestGateway):
    def _sign(
        self,
        timestamp_ms: str,
        method: str,
        path: str,
        secret: str,
        *,
        params: dict[str, Any] | None = None,
        body: str = "",
    ) -> str:
        query = ""
        if params:
            query = "?" + urlencode(dict(sorted(params.items())), doseq=True)
        payload = f"{timestamp_ms}{method.upper()}{path}{query}{body}"
        return base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

    def _headers(
        self,
        api_key: str,
        secret: str,
        passphrase: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        timestamp_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        return {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": self._sign(timestamp_ms, "GET", path, secret, params=params),
            "ACCESS-TIMESTAMP": timestamp_ms,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        path: str,
        api_key: str,
        secret: str,
        passphrase: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        payload = await self._json_request(
            "GET",
            f"https://api.bitget.com{path}",
            params=params,
            headers=self._headers(api_key, secret, passphrase, path, params=params),
        )
        if payload.get("code") not in {"00000", None}:
            raise RuntimeError(f"Bitget API error: {payload}")
        return payload.get("data", []) or []

    async def _fetch_spot_tickers(self) -> dict[str, dict[str, Any]]:
        payload = await self._json_request(
            "GET",
            "https://api.bitget.com/api/v2/spot/market/tickers",
        )
        tickers: dict[str, dict[str, Any]] = {}
        for item in payload.get("data", []) or []:
            symbol = str(item.get("symbol", ""))
            if not symbol:
                continue
            normalized = symbol.replace("USDT", "/USDT").replace("USDC", "/USDC")
            tickers[normalized] = {"last": _safe_float(item, "lastPr")}
        return tickers

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        passphrase = config.get("password", "")
        if not api_key or not secret or not passphrase:
            raise ValueError("Bitget API key/secret/password is required for REST mode")

        tickers = await self._fetch_spot_tickers()
        spot = await self._request(
            "/api/v2/spot/account/assets", api_key, secret, passphrase
        )
        margin = await self._request(
            "/api/v2/margin/crossed/account/assets", api_key, secret, passphrase
        )
        linear = await self._request(
            "/api/v2/mix/account/accounts",
            api_key,
            secret,
            passphrase,
            params={"productType": "USDT-FUTURES"},
        )
        inverse = await self._request(
            "/api/v2/mix/account/accounts",
            api_key,
            secret,
            passphrase,
            params={"productType": "COIN-FUTURES"},
        )

        payload = [
            {
                "account_type": "spot",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item.get("coin") or item.get("asset"),
                        "amount": _safe_float(item, "available")
                        + _safe_float(item, "locked"),
                    }
                    for item in spot
                ],
            },
            {
                "account_type": "margin",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item.get("coin"),
                        "amount": _safe_float(item, "totalAmount", "available"),
                    }
                    for item in margin
                ],
            },
            {
                "account_type": "linear_futures",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item.get("marginCoin"),
                        "amount": _safe_float(
                            item, "available", "accountEquity", "equity"
                        ),
                    }
                    for item in linear
                ],
            },
            {
                "account_type": "inverse_futures",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item.get("marginCoin"),
                        "amount": _safe_float(
                            item, "available", "accountEquity", "equity"
                        ),
                    }
                    for item in inverse
                ],
            },
        ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class GateIoRestBalanceGateway(BaseRestGateway):
    def _sign(
        self,
        method: str,
        request_path: str,
        query_string: str,
        body: str,
        timestamp: str,
        secret: str,
    ) -> str:
        body_hash = hashlib.sha512(body.encode("utf-8")).hexdigest()
        payload = "\n".join(
            [method.upper(), request_path, query_string, body_hash, timestamp]
        )
        return hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

    async def _request(
        self,
        path: str,
        api_key: str,
        secret: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        request_path = f"/api/v4{path}"
        query_string = urlencode(params, doseq=True) if params else ""
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        headers = {
            "KEY": api_key,
            "Timestamp": timestamp,
            "SIGN": self._sign(
                "GET", request_path, query_string, "", timestamp, secret
            ),
            "Content-Type": "application/json",
        }
        return await self._json_request(
            "GET",
            f"https://api.gateio.ws{request_path}",
            params=params,
            headers=headers,
        )

    async def _fetch_spot_tickers(self) -> dict[str, dict[str, Any]]:
        rows = await self._json_request(
            "GET", "https://api.gateio.ws/api/v4/spot/tickers"
        )
        tickers: dict[str, dict[str, Any]] = {}
        for item in rows:
            pair = str(item.get("currency_pair", ""))
            if "_" not in pair:
                continue
            base, quote = pair.split("_", 1)
            tickers[f"{base}/{quote}"] = {"last": _safe_float(item, "last")}
        return tickers

    async def _detect_unified_account(
        self,
        api_key: str,
        secret: str,
        futures_data: dict,
        spot_assets: list[dict] | None = None,
    ) -> bool:
        """Detect whether Gate.io account uses unified margin mode.

        In unified mode, spot and futures share the same USDT balance.
        We detect this by comparing the USDT spot amount to futures total:
        if they are approximately equal (within 5%), it is likely unified.
        """
        futures_total = _safe_float(futures_data, "available", "total")
        if futures_total <= 0:
            return False

        if spot_assets:
            for a in spot_assets:
                if str(a.get("coin", "")).upper() == "USDT":
                    spot_usdt = float(a.get("amount", 0) or 0)
                    if spot_usdt > 0 and abs(spot_usdt - futures_total) / max(spot_usdt, futures_total) < 0.05:
                        return True
                    break

        # A matching USDT balance is the only evidence that these views mirror
        # one unified account; otherwise preserve and count them separately.
        return False

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCCTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        if not api_key or not secret:
            raise ValueError("Gate.io API key/secret is required for REST mode")

        tickers = await self._fetch_spot_tickers()
        spot = await self._request("/spot/accounts", api_key, secret)
        margin = await self._request("/margin/accounts", api_key, secret)
        futures = await self._request("/futures/usdt/accounts", api_key, secret)
        futures_total_amount = _safe_float(futures, "available", "total")

        spot_assets = [
            {
                "coin": item.get("currency"),
                "amount": _safe_float(item, "available")
                + _safe_float(item, "locked"),
            }
            for item in spot
        ]
        margin_assets = [
            {
                "coin": item.get("currency"),
                "amount": _safe_float(item, "available")
                + _safe_float(item, "locked"),
            }
            for item in margin
            if item.get("currency")
        ]

        # Detect unified account: if futures has balance, it is likely
        # double-counted since unified accounts share USDT across
        # spot and futures. In unified mode, futures USDT = spot USDT,
        # so we skip futures and show the full balance under both labels.
        is_unified = await self._detect_unified_account(api_key, secret, futures, spot_assets=spot_assets)

        if is_unified:
            payload = [
                {
                    "account_type": "spot",
                    "tickers": tickers,
                    "assets": spot_assets,
                },
                {
                    "account_type": "margin",
                    "tickers": tickers,
                    "assets": margin_assets,
                },
                {
                    "account_type": "usdt_futures",
                    "tickers": tickers,
                    "assets": spot_assets,
                    "mirror_of": "spot",
                },
            ]
        else:
            payload = [
                {
                    "account_type": "spot",
                    "tickers": tickers,
                    "assets": spot_assets,
                },
                {
                    "account_type": "margin",
                    "tickers": tickers,
                    "assets": margin_assets,
                },
                {
                    "account_type": "usdt_futures",
                    "tickers": tickers,
                    "assets": [
                        {
                            "coin": "USDT",
                            "amount": futures_total_amount,
                        }
                    ],
                },
            ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class KucoinRestBalanceGateway(BaseRestGateway):
    def _sign(self, payload: str, secret: str) -> str:
        return base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

    async def _request(
        self,
        path: str,
        api_key: str,
        secret: str,
        passphrase: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        query_string = urlencode(params, doseq=True) if params else ""
        endpoint = f"/api/v1{path}"
        if query_string:
            endpoint = f"{endpoint}?{query_string}"
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        headers = {
            "KC-API-KEY-VERSION": "2",
            "KC-API-KEY": api_key,
            "KC-API-TIMESTAMP": timestamp,
            "KC-API-PASSPHRASE": self._sign(passphrase, secret),
            "KC-API-SIGN": self._sign(f"{timestamp}GET{endpoint}", secret),
        }
        payload = await self._json_request(
            "GET",
            f"https://api.kucoin.com{endpoint}",
            headers=headers,
        )
        if payload.get("code") not in {"200000", None}:
            raise RuntimeError(f"KuCoin API error: {payload}")
        return payload.get("data", []) or []

    async def _fetch_tickers(self) -> dict[str, dict[str, Any]]:
        payload = await self._json_request(
            "GET",
            "https://api.kucoin.com/api/v1/market/allTickers",
        )
        tickers: dict[str, dict[str, Any]] = {}
        for item in payload.get("data", {}).get("ticker", []):
            symbol = str(item.get("symbol", ""))
            if "-" not in symbol:
                continue
            tickers[symbol.replace("-", "/")] = {"last": _safe_float(item, "last")}
        return tickers

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        passphrase = config.get("password", "")
        if not api_key or not secret or not passphrase:
            raise ValueError("KuCoin API key/secret/password is required for REST mode")

        tickers = await self._fetch_tickers()
        trade = await self._request(
            "/accounts",
            api_key,
            secret,
            passphrase,
            params={"type": "trade"},
        )
        main = await self._request(
            "/accounts",
            api_key,
            secret,
            passphrase,
            params={"type": "main"},
        )

        payload = [
            {
                "account_type": "trade",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item.get("currency"),
                        "amount": _safe_float(item, "balance"),
                    }
                    for item in trade
                ],
            },
            {
                "account_type": "main",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item.get("currency"),
                        "amount": _safe_float(item, "balance"),
                    }
                    for item in main
                ],
            },
        ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class MexcRestBalanceGateway(BaseRestGateway):
    def _spot_signed_params(
        self, secret: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        signed = dict(params or {})
        signed["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        signed["recvWindow"] = 5000
        query = urlencode(signed, doseq=True)
        signed["signature"] = hmac.new(
            secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signed

    def _contract_headers(
        self,
        api_key: str,
        secret: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        ordered = dict(sorted((params or {}).items()))
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        query = urlencode(ordered, doseq=True)
        auth = f"{api_key}{timestamp}{query}"
        signature = hmac.new(
            secret.encode("utf-8"),
            auth.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "ApiKey": api_key,
            "Request-Time": timestamp,
            "Signature": signature,
            "Content-Type": "application/json",
        }
        return headers, ordered

    async def _fetch_tickers(self) -> dict[str, dict[str, Any]]:
        rows = await self._json_request(
            "GET", "https://api.mexc.com/api/v3/ticker/price"
        )
        tickers: dict[str, dict[str, Any]] = {}
        for item in rows:
            symbol = str(item.get("symbol", ""))
            if symbol.endswith("USDT"):
                tickers[f"{symbol[:-4]}/USDT"] = {"last": _safe_float(item, "price")}
        return tickers

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        if not api_key or not secret:
            raise ValueError("MEXC API key/secret is required for REST mode")

        tickers = await self._fetch_tickers()
        spot = await self._json_request(
            "GET",
            "https://api.mexc.com/api/v3/account",
            params=self._spot_signed_params(secret),
            headers={"X-MEXC-APIKEY": api_key},
        )
        contract_headers, contract_params = self._contract_headers(api_key, secret)
        futures = await self._json_request(
            "GET",
            "https://contract.mexc.com/api/v1/private/account/assets",
            params=contract_params,
            headers=contract_headers,
        )

        payload = [
            {
                "account_type": "spot",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item.get("asset"),
                        "amount": _safe_float(item, "free")
                        + _safe_float(item, "locked"),
                    }
                    for item in spot.get("balances", [])
                ],
            },
            {
                "account_type": "usdt_futures",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item.get("currency"),
                        "amount": _safe_float(item, "availableBalance", "equity"),
                    }
                    for item in futures.get("data", []) or []
                ],
            },
        ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class HtxRestBalanceGateway(BaseRestGateway):
    hostname = "api.htx.com"

    def _sign(self, method: str, path: str, params: dict[str, Any], secret: str) -> str:
        encoded = urlencode(dict(sorted(params.items())), doseq=True)
        payload = "\n".join([method.upper(), self.hostname, path, encoded])
        return base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

    async def _request(self, path: str, api_key: str, secret: str) -> Any:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        params = {
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "AccessKeyId": api_key,
            "Timestamp": timestamp,
        }
        params["Signature"] = self._sign("GET", path, params, secret)
        payload = await self._json_request(
            "GET",
            f"https://{self.hostname}{path}",
            params=params,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if isinstance(payload, dict) and payload.get("status") == "error":
            code = payload.get("err-code") or "unknown"
            msg = payload.get("err-msg") or "HTX API error"
            raise RuntimeError(f"HTX API error {code}: {msg}")
        return payload

    async def _fetch_tickers(self) -> dict[str, dict[str, Any]]:
        payload = await self._json_request(
            "GET", f"https://{self.hostname}/market/tickers"
        )
        tickers: dict[str, dict[str, Any]] = {}
        for item in payload.get("data", []) or []:
            symbol = str(item.get("symbol", ""))
            if symbol.endswith("usdt"):
                tickers[f"{symbol[:-4].upper()}/USDT"] = {
                    "last": _safe_float(item, "close")
                }
        return tickers

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        if not api_key or not secret:
            raise ValueError("HTX API key/secret is required for REST mode")

        accounts_payload = await self._request("/v1/account/accounts", api_key, secret)
        accounts = accounts_payload.get("data", []) or []
        tickers = await self._fetch_tickers()
        balances: list[dict[str, Any]] = []
        for account in accounts:
            account_id = account.get("id")
            state = account.get("state")
            if not account_id or state != "working":
                continue
            balance_payload = await self._request(
                f"/v1/account/accounts/{account_id}/balance", api_key, secret
            )
            balances.extend(balance_payload.get("data", {}).get("list", []) or [])

        by_coin: dict[str, float] = {}
        for item in balances:
            coin = str(item.get("currency", "")).upper()
            by_coin[coin] = by_coin.get(coin, 0.0) + _safe_float(item, "balance")

        payload = [
            {
                "account_type": "spot",
                "tickers": tickers,
                "assets": [
                    {"coin": coin, "amount": amount} for coin, amount in by_coin.items()
                ],
            }
        ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class BitmartRestBalanceGateway(BaseRestGateway):
    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        return await manager._fetch_bitmart_balance_direct(
            config_override=config_override,
        )


class PoloniexRestBalanceGateway(BaseRestGateway):
    def _headers(
        self, api_key: str, secret: str, path: str, query: dict[str, Any] | None = None
    ) -> dict[str, str]:
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        auth = "GET\n" + path + "\n"
        sorted_query = dict(sorted((query or {}).items()))
        sorted_query["signTimestamp"] = timestamp
        auth += urlencode(sorted_query, doseq=True)
        signature = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"), auth.encode("utf-8"), hashlib.sha256
            ).digest()
        ).decode("utf-8")
        return {
            "Content-Type": "application/json",
            "key": api_key,
            "signTimestamp": timestamp,
            "signature": signature,
        }

    async def _fetch_tickers(self) -> dict[str, dict[str, Any]]:
        rows = await self._json_request(
            "GET", "https://api.poloniex.com/markets/ticker24h"
        )
        tickers: dict[str, dict[str, Any]] = {}
        for item in rows:
            symbol = str(item.get("symbol", ""))
            if "_" in symbol:
                base, quote = symbol.split("_", 1)
                tickers[f"{base}/{quote}"] = {"last": _safe_float(item, "close")}
        return tickers

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        if not api_key or not secret:
            raise ValueError("Poloniex API key/secret is required for REST mode")

        tickers = await self._fetch_tickers()
        spot = await self._json_request(
            "GET",
            "https://api.poloniex.com/accounts/balances",
            params={"accountType": "SPOT"},
            headers=self._headers(
                api_key, secret, "/accounts/balances", {"accountType": "SPOT"}
            ),
        )
        try:
            futures = await self._json_request(
                "GET",
                "https://futures-api.poloniex.com/v3/account/balance",
                headers=self._headers(api_key, secret, "/v3/account/balance"),
            )
        except (aiohttp.ClientResponseError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            response_status = getattr(exc, "status", None)
            if response_status is None or response_status in {401, 403, 404, 503}:
                logger.warning(
                    "Poloniex futures balance endpoint unavailable; returning partial balance"
                )
                futures = None
            else:
                raise

        payload = [
            {
                "account_type": "spot",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item.get("currency"),
                        "amount": _safe_float(item, "available")
                        + _safe_float(item, "hold"),
                    }
                    for account in spot
                    for item in account.get("balances", [])
                ],
            },
            {
                "account_type": "usdt_futures",
                "tickers": tickers,
                "assets": [
                    {
                        "coin": item.get("ccy"),
                        "amount": _safe_float(item, "eq", "avail"),
                    }
                    for item in ((futures or {}).get("data", {}) or {}).get("details", [])
                ],
                "error": "account balance unavailable" if futures is None else None,
            },
        ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class LbankRestBalanceGateway(BaseRestGateway):
    def _signed_body(self, api_key: str, secret: str) -> tuple[dict[str, str], str]:
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        echostr = hashlib.md5(timestamp.encode("utf-8")).hexdigest()
        query = {
            "api_key": api_key,
            "echostr": echostr,
            "signature_method": "HmacSHA256",
            "timestamp": timestamp,
        }
        auth = urlencode(dict(sorted(query.items())), doseq=True)
        md5_hash = hashlib.md5(auth.encode("utf-8")).hexdigest().upper()
        sign = hmac.new(
            secret.encode("utf-8"), md5_hash.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        query["sign"] = sign
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "timestamp": timestamp,
            "signature_method": "HmacSHA256",
            "echostr": echostr,
        }
        return headers, urlencode(dict(sorted(query.items())), doseq=True)

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        if not api_key or not secret:
            raise ValueError("LBank API key/secret is required for REST mode")

        headers, body = self._signed_body(api_key, secret)
        payload = await self._json_request(
            "POST",
            "https://api.lbkex.com/v2/supplement/user_info_account.do",
            headers=headers,
            data=body,
        )
        rows = (payload.get("data", {}) or {}).get("balances", []) or []
        merged_payload = [
            {
                "account_type": "spot",
                "assets": [
                    {
                        "coin": item.get("asset"),
                        "amount": _safe_float(item, "free")
                        + _safe_float(item, "locked"),
                    }
                    for item in rows
                ],
            }
        ]
        return _merge_accounts(
            exchange_id, merged_payload, manager.calculate_usd_value_sync
        )


class CoinexRestBalanceGateway(BaseRestGateway):
    def _headers(
        self,
        api_key: str,
        secret: str,
        method: str,
        path: str,
        query: dict[str, Any],
    ) -> tuple[dict[str, str], dict[str, Any]]:
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        params = dict(query)
        encoded = urlencode(dict(sorted(params.items())), doseq=True)
        prepared_path = f"/v2{path}"
        if encoded:
            prepared_path = f"{prepared_path}?{encoded}"
        payload = f"{method.upper()}{prepared_path}{timestamp}"
        signature = (
            hmac.new(
                secret.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            )
            .hexdigest()
            .lower()
        )
        headers = {
            "X-COINEX-KEY": api_key,
            "X-COINEX-SIGN": signature,
            "X-COINEX-TIMESTAMP": timestamp,
        }
        return headers, params

    async def _request(self, path: str, api_key: str, secret: str) -> Any:
        headers, params = self._headers(api_key, secret, "GET", path, {})
        payload = await self._json_request(
            "GET",
            f"https://api.coinex.com/v2{path}",
            params=params,
            headers=headers,
        )
        if payload.get("code") not in {0, "0", None}:
            raise RuntimeError(f"CoinEx API error: {payload}")
        return payload.get("data", []) or []

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        if not api_key or not secret:
            raise ValueError("CoinEx API key/secret is required for REST mode")

        spot = await self._request("/assets/spot/balance", api_key, secret)
        margin = await self._request("/assets/margin/balance", api_key, secret)
        futures = await self._request("/assets/futures/balance", api_key, secret)

        payload = [
            {
                "account_type": "spot",
                "assets": [
                    {
                        "coin": item.get("ccy") or item.get("currency"),
                        "amount": _safe_float(item, "available", "balance"),
                    }
                    for item in spot
                ],
            },
            {
                "account_type": "margin",
                "assets": [
                    {
                        "coin": item.get("ccy") or item.get("currency"),
                        "amount": _safe_float(item, "available", "balance"),
                    }
                    for item in margin
                ],
            },
            {
                "account_type": "usdt_futures",
                "assets": [
                    {
                        "coin": item.get("ccy") or item.get("currency"),
                        "amount": _safe_float(item, "available", "balance"),
                    }
                    for item in futures
                ],
            },
        ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class BingxRestBalanceGateway(BaseRestGateway):
    def _signed_params(
        self, secret: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        signed = dict(params or {})
        signed["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        encoded = urlencode(dict(sorted(signed.items())), doseq=True)
        signed["signature"] = hmac.new(
            secret.encode("utf-8"),
            encoded.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signed

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        if not api_key or not secret:
            raise ValueError("BingX API key/secret is required for REST mode")

        headers = {"X-BX-APIKEY": api_key, "X-SOURCE-KEY": "CCXT"}
        spot = await self._json_request(
            "GET",
            "https://open-api.bingx.com/openApi/spot/v1/account/balance",
            params=self._signed_params(secret),
            headers=headers,
        )
        swap = await self._json_request(
            "GET",
            "https://open-api.bingx.com/openApi/swap/v2/user/balance",
            params=self._signed_params(secret),
            headers=headers,
        )

        payload = [
            {
                "account_type": "spot",
                "assets": [
                    {
                        "coin": item.get("asset"),
                        "amount": _safe_float(item, "free")
                        + _safe_float(item, "locked"),
                    }
                    for item in (spot.get("data", {}) or {}).get("balances", [])
                ],
            },
            {
                "account_type": "usdt_futures",
                "assets": [
                    {
                        "coin": (swap.get("data", {}) or {})
                        .get("balance", {})
                        .get("asset"),
                        "amount": _safe_float(
                            (swap.get("data", {}) or {}).get("balance", {}),
                            "equity",
                            "balance",
                        ),
                    }
                ],
            },
        ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class XtRestBalanceGateway(BaseRestGateway):
    def _headers(
        self,
        api_key: str,
        secret: str,
        payload_string: str,
        *,
        recv_window: str | None = None,
    ) -> dict[str, str]:
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        headers = {
            "Content-Type": "application/json",
            "xt-validate-appkey": api_key,
            "xt-validate-timestamp": timestamp,
        }
        if recv_window is not None:
            headers["xt-validate-algorithms"] = "HmacSHA256"
            headers["xt-validate-recvwindow"] = recv_window
        headers["xt-validate-signature"] = hmac.new(
            secret.encode("utf-8"), payload_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return headers

    async def fetch_balance(
        self,
        exchange_id: str,
        manager: "CCXTManager",
        config_override: dict[str, Any] | None = None,
    ) -> ServiceBalanceSchema:
        config = self._resolve_config(exchange_id, config_override)
        api_key = config.get("apiKey", "")
        secret = config.get("secret", "")
        if not api_key or not secret:
            raise ValueError("XT API key/secret is required for REST mode")

        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        spot_payload = f"xt-validate-algorithms=HmacSHA256&xt-validate-appkey={api_key}&xt-validate-recvwindow=5000&xt-validate-timestamp={timestamp}#GET#/v4/balances"
        spot_headers = {
            "Content-Type": "application/json",
            "xt-validate-algorithms": "HmacSHA256",
            "xt-validate-appkey": api_key,
            "xt-validate-recvwindow": "5000",
            "xt-validate-timestamp": timestamp,
            "xt-validate-signature": hmac.new(
                secret.encode("utf-8"), spot_payload.encode("utf-8"), hashlib.sha256
            ).hexdigest(),
        }
        spot = await self._json_request(
            "GET", "https://sapi.xt.com/v4/balances", headers=spot_headers
        )

        futures_error = None
        try:
            timestamp2 = str(int(datetime.now(timezone.utc).timestamp() * 1000))
            futures_payload = f"xt-validate-appkey={api_key}&xt-validate-timestamp={timestamp2}#/future/user/v1/balance/list"
            futures_headers = {
                "Content-Type": "application/json",
                "xt-validate-appkey": api_key,
                "xt-validate-timestamp": timestamp2,
                "xt-validate-signature": hmac.new(
                    secret.encode("utf-8"),
                    futures_payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest(),
            }
            futures = await self._json_request(
                "GET",
                "https://fapi.xt.com/future/user/v1/balance/list",
                headers=futures_headers,
            )
        except Exception as exc:
            logger.warning("XT futures balance fetch failed, skipping: %s", exc)
            futures = {}
            futures_error = "account balance unavailable"

        payload = [
            {
                "account_type": "spot",
                "assets": [
                    {
                        "coin": item.get("currency"),
                        "amount": _safe_float(item, "totalAmount", "availableAmount"),
                        "value_usd": _safe_float(item, "convertUsdtAmount"),
                    }
                    for item in (spot.get("result", {}) or {}).get("assets", [])
                ],
            },
            {
                "account_type": "usdt_futures",
                "assets": [
                    {
                        "coin": item.get("coin"),
                        "amount": _safe_float(item, "walletBalance"),
                    }
                    for item in futures.get("result", []) or []
                ],
                "error": futures_error,
            },
        ]
        return _merge_accounts(exchange_id, payload, manager.calculate_usd_value_sync)


class ExchangeBalanceGatewayRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._ccxt_gateway = CCXTBalanceGateway()
        self._rest_gateways: dict[str, BalanceGateway] = {
            "binance": BinanceRestBalanceGateway(settings),
            "okx": OkxRestBalanceGateway(settings),
            "bybit": BybitRestBalanceGateway(settings),
            "bitget": BitgetRestBalanceGateway(settings),
            "gateio": GateIoRestBalanceGateway(settings),
            "htx": HtxRestBalanceGateway(settings),
            "kucoin": KucoinRestBalanceGateway(settings),
            "mexc": MexcRestBalanceGateway(settings),
            "bitmart": BitmartRestBalanceGateway(settings),
            "poloniex": PoloniexRestBalanceGateway(settings),
            "lbank": LbankRestBalanceGateway(settings),
            "coinex": CoinexRestBalanceGateway(settings),
            "bingx": BingxRestBalanceGateway(settings),
            "xt": XtRestBalanceGateway(settings),
        }

    def resolve(self, exchange_id: str) -> BalanceGateway:
        override = self.settings.exchange_transport_overrides_map.get(exchange_id)
        selected = override or self.settings.exchange_default_transport
        if selected == "rest" and exchange_id in self._rest_gateways:
            return self._rest_gateways[exchange_id]
        return self._ccxt_gateway

    def resolve_rest(self, exchange_id: str) -> BalanceGateway:
        gateway = self._rest_gateways.get(exchange_id)
        if gateway is None:
            raise KeyError(f"REST gateway is not configured for {exchange_id}")
        return gateway

    def supported_rest_exchanges(self) -> set[str]:
        return set(self._rest_gateways)

    async def close_all(self) -> None:
        for gateway in self._rest_gateways.values():
            close = getattr(gateway, "close", None)
            if callable(close):
                await close()


_registry_cache: dict[int, ExchangeBalanceGatewayRegistry] = {}


def get_balance_gateway_registry(settings: Settings) -> ExchangeBalanceGatewayRegistry:
    cache_key = id(settings)
    registry = _registry_cache.get(cache_key)
    if registry is None:
        registry = ExchangeBalanceGatewayRegistry(settings)
        _registry_cache[cache_key] = registry
    return registry
