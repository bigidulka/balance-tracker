import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import ccxt.async_support as ccxt


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ccxt_manager import EXCHANGE_ACCOUNT_TYPES, EXCHANGE_OPTIONS, RAW_BALANCE_METHOD_CANDIDATES


COIN_KEYS = (
    "ccy",
    "currency",
    "asset",
    "coin",
    "coinName",
    "currencyName",
    "token",
)
FREE_KEYS = (
    "free",
    "available",
    "availableBalance",
    "availBal",
    "availEq",
    "transferable",
    "can_withdraw",
)
USED_KEYS = (
    "used",
    "frozen",
    "frozenBal",
    "locked",
    "hold",
    "holds",
    "ordFrozen",
    "freeze",
)
TOTAL_KEYS = (
    "total",
    "balance",
    "bal",
    "walletBalance",
    "equity",
    "cashBal",
    "amount",
)


@dataclass
class AssetBalance:
    coin: str
    free: float
    used: float
    total: float


@dataclass
class AccountSnapshot:
    account_type: str
    request_params: dict[str, Any]
    assets: list[AssetBalance]
    raw_method: str
    error: str | None = None


def _safe_float(value: Any) -> float:
    if value in (None, "", False):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return 0.0


def _first_value(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _extract_coin(payload: dict[str, Any]) -> str | None:
    coin = _first_value(payload, COIN_KEYS)
    if isinstance(coin, str) and coin.strip():
        return coin.strip().upper()
    return None


def _looks_like_balance_node(payload: dict[str, Any]) -> bool:
    if _extract_coin(payload) is None:
        return False
    return any(key in payload for key in FREE_KEYS + USED_KEYS + TOTAL_KEYS)


def _walk_balance_nodes(payload: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    stack: list[Any] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if _looks_like_balance_node(current):
                nodes.append(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return nodes


def _normalize_from_generic_payload(payload: Any) -> dict[str, dict[str, float]]:
    balances: dict[str, dict[str, float]] = {}
    for node in _walk_balance_nodes(payload):
        coin = _extract_coin(node)
        if coin is None:
            continue

        free = _safe_float(_first_value(node, FREE_KEYS))
        used = _safe_float(_first_value(node, USED_KEYS))
        total_value = _first_value(node, TOTAL_KEYS)
        total = _safe_float(total_value)
        if total == 0.0 and (free > 0.0 or used > 0.0):
            total = free + used
        if free == 0.0 and total > 0.0 and used == 0.0:
            free = total
        if total <= 0.0 and free <= 0.0 and used <= 0.0:
            continue

        record = balances.setdefault(coin, {"free": 0.0, "used": 0.0, "total": 0.0})
        record["free"] += free
        record["used"] += used
        record["total"] += total
    return balances


def _balance_dict_to_assets(balance: dict[str, Any]) -> list[AssetBalance]:
    totals = balance.get("total", {}) or {}
    free_map = balance.get("free", {}) or {}
    used_map = balance.get("used", {}) or {}
    assets: list[AssetBalance] = []
    for coin, total in totals.items():
        total_f = _safe_float(total)
        free_f = _safe_float(free_map.get(coin))
        used_f = _safe_float(used_map.get(coin))
        if total_f <= 0 and free_f <= 0 and used_f <= 0:
            continue
        assets.append(
            AssetBalance(
                coin=str(coin).upper(),
                free=free_f,
                used=used_f,
                total=total_f if total_f > 0 else free_f + used_f,
            )
        )
    return sorted(assets, key=lambda item: item.total, reverse=True)


def _generic_balances_to_assets(balances: dict[str, dict[str, float]]) -> list[AssetBalance]:
    assets: list[AssetBalance] = []
    for coin, amounts in balances.items():
        total = amounts["total"] if amounts["total"] > 0 else amounts["free"] + amounts["used"]
        if total <= 0 and amounts["free"] <= 0 and amounts["used"] <= 0:
            continue
        assets.append(
            AssetBalance(
                coin=coin,
                free=amounts["free"],
                used=amounts["used"],
                total=total,
            )
        )
    return sorted(assets, key=lambda item: item.total, reverse=True)


def _route_key(params: dict[str, Any]) -> str:
    balance_type = str(params.get("type", "__default__"))
    subtype = str(params.get("subType", ""))
    settle = str(params.get("settle", ""))
    return f"type={balance_type}|subType={subtype}|settle={settle}"


def _build_exchange(exchange_id: str, args: argparse.Namespace):
    exchange_class = getattr(ccxt, exchange_id)
    config: dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": args.timeout_ms,
        "apiKey": args.api_key or os.getenv(f"{exchange_id.upper()}_API_KEY", ""),
        "secret": args.secret or os.getenv(f"{exchange_id.upper()}_SECRET", ""),
        "password": args.password or os.getenv(f"{exchange_id.upper()}_PASSWORD", ""),
        "uid": args.uid or os.getenv(f"{exchange_id.upper()}_UID", ""),
    }
    if exchange_id in EXCHANGE_OPTIONS:
        config["options"] = EXCHANGE_OPTIONS[exchange_id]
    if args.verbose:
        config["verbose"] = True
    return exchange_class(config)


async def _call_raw_balance(exchange: Any, exchange_id: str, params: dict[str, Any]) -> tuple[str, Any]:
    request_params = dict(params)

    if exchange_id == "binance":
        balance_type = request_params.get("type", "spot")
        query = {k: v for k, v in request_params.items() if k != "type"}
        if balance_type == "funding":
            return "sapiPostAssetGetFundingAsset", await exchange.sapiPostAssetGetFundingAsset(query)
        if balance_type == "margin":
            return "sapiGetMarginAccount", await exchange.sapiGetMarginAccount(query)
        if balance_type in {"future", "swap", "linear"}:
            return "fapiPrivateV3GetAccount", await exchange.fapiPrivateV3GetAccount(query)
        if balance_type in {"delivery", "inverse"}:
            return "dapiPrivateGetAccount", await exchange.dapiPrivateGetAccount(query)
        return "privateGetAccount", await exchange.privateGetAccount(query)

    if exchange_id == "okx":
        balance_type = request_params.get("type", "trading")
        query = {k: v for k, v in request_params.items() if k != "type"}
        if balance_type == "funding":
            return "privateGetAssetBalances", await exchange.privateGetAssetBalances(query)
        return "privateGetAccountBalance", await exchange.privateGetAccountBalance(query)

    candidates = RAW_BALANCE_METHOD_CANDIDATES.get(exchange_id, {})
    route = _route_key(request_params)
    method_names = candidates.get(route) or candidates.get("type=__default__|subType=|settle=") or []
    if not method_names:
        raise ValueError(f"No raw implicit method candidates configured for {exchange_id} route {route}")

    for method_name in method_names:
        if not hasattr(exchange, method_name):
            continue
        method = getattr(exchange, method_name)
        try:
            return method_name, await method(request_params)
        except Exception:
            if method_name == method_names[-1]:
                raise
    raise ValueError(f"No usable raw implicit methods found for {exchange_id}")


def _normalize_raw_balance(exchange: Any, exchange_id: str, params: dict[str, Any], payload: Any) -> list[AssetBalance]:
    if exchange_id == "binance":
        balance_type = params.get("type", "spot")
        parsed = exchange.parse_balance_custom(payload, balance_type)
        return _balance_dict_to_assets(exchange.safe_balance(parsed))

    if exchange_id == "okx":
        balance_type = params.get("type", "trading")
        parsed = exchange.parse_balance_by_type(balance_type, payload)
        return _balance_dict_to_assets(exchange.safe_balance(parsed))

    generic = _normalize_from_generic_payload(payload)
    return _generic_balances_to_assets(generic)


async def _probe_exchange(args: argparse.Namespace) -> dict[str, Any]:
    exchange_id = args.exchange.lower()
    exchange = _build_exchange(exchange_id, args)
    account_configs = EXCHANGE_ACCOUNT_TYPES.get(exchange_id, [{"type": "spot", "params": {}}])
    snapshots: list[AccountSnapshot] = []
    raw_payloads: dict[str, Any] = {}

    try:
        for account_config in account_configs:
            account_type = account_config["type"]
            params = dict(account_config.get("params", {}))
            try:
                method_name, payload = await _call_raw_balance(exchange, exchange_id, params)
                assets = _normalize_raw_balance(exchange, exchange_id, params, payload)
                snapshots.append(
                    AccountSnapshot(
                        account_type=account_type,
                        request_params=params,
                        assets=assets,
                        raw_method=method_name,
                    )
                )
                if args.include_raw:
                    raw_payloads[f"{account_type}:{method_name}"] = payload
            except Exception as exc:
                snapshots.append(
                    AccountSnapshot(
                        account_type=account_type,
                        request_params=params,
                        assets=[],
                        raw_method="",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
    finally:
        await exchange.close()

    aggregated: dict[str, AssetBalance] = {}
    for snapshot in snapshots:
        for asset in snapshot.assets:
            current = aggregated.get(asset.coin)
            if current is None:
                aggregated[asset.coin] = AssetBalance(asset.coin, asset.free, asset.used, asset.total)
            else:
                aggregated[asset.coin] = AssetBalance(
                    coin=asset.coin,
                    free=current.free + asset.free,
                    used=current.used + asset.used,
                    total=current.total + asset.total,
                )

    result: dict[str, Any] = {
        "exchange": exchange_id,
        "snapshots": [
            {
                "account_type": snapshot.account_type,
                "request_params": snapshot.request_params,
                "raw_method": snapshot.raw_method,
                "error": snapshot.error,
                "asset_count": len(snapshot.assets),
                "assets": [asdict(asset) for asset in snapshot.assets],
            }
            for snapshot in snapshots
        ],
        "aggregated": {
            "asset_count": len(aggregated),
            "assets": [asdict(asset) for asset in sorted(aggregated.values(), key=lambda item: item.total, reverse=True)],
        },
    }
    if args.include_raw:
        result["raw"] = raw_payloads
    return result


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Raw ccxt balance probe without load_markets() and without fetch_balance()."
    )
    parser.add_argument("--exchange", required=True, help="Exchange id, e.g. okx, binance, bybit")
    parser.add_argument("--api-key", default="", help="API key, falls back to <EXCHANGE>_API_KEY")
    parser.add_argument("--secret", default="", help="API secret, falls back to <EXCHANGE>_SECRET")
    parser.add_argument("--password", default="", help="API password/passphrase")
    parser.add_argument("--uid", default="", help="Optional uid")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--include-raw", action="store_true", help="Include raw exchange payloads")
    parser.add_argument("--verbose", action="store_true", help="Enable ccxt verbose mode")
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    result = asyncio.run(_probe_exchange(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
