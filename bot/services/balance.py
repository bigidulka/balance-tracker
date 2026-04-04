"""Balance data fetch and parsing helpers."""

from __future__ import annotations

from typing import Any

from bot.api_client import api_client
from bot.config import settings

_balance_cache: dict[str, dict[str, Any]] = {}


def filter_assets(assets: list[dict[str, Any]], hide_small: bool) -> list[dict[str, Any]]:
    if not hide_small:
        return assets
    return [
        asset
        for asset in assets
        if asset.get("value_usd", 0) >= settings.hide_small_balance_threshold
    ]


async def get_balance_data(force_update_cache: bool = False) -> dict[str, Any]:
    global _balance_cache
    cache_key = api_client.current_identity_cache_key()
    try:
        _balance_cache[cache_key] = await api_client.get_balances(cached=True)
    except Exception:
        if force_update_cache or cache_key not in _balance_cache:
            raise
    return _balance_cache.get(cache_key, {})


def parse_balances(data: dict[str, Any]) -> dict[str, Any]:
    services = data.get("services", [])

    spot_total = 0.0
    futures_total = 0.0
    dex_total = 0.0
    exchanges_count = 0

    spot_exchanges: dict[str, dict[str, Any]] = {}
    futures_exchanges: dict[str, dict[str, Any]] = {}
    dex_wallets: dict[str, dict[str, Any]] = {}

    for svc in services:
        name = svc.get("service", "")
        integration_id = svc.get("integration_id")
        key = f"{name}#{integration_id}" if integration_id is not None else name
        accounts = svc.get("accounts", [])
        service_assets = svc.get("assets", [])
        service_total = svc.get("total_usd", 0)

        if name.startswith("okx_wallet"):
            if accounts:
                for acc in accounts:
                    if acc.get("account_type") == "spot":
                        dex_total += acc.get("total_usd", 0)
                        dex_wallets[key] = {**acc, "service": name, "integration_id": integration_id}
            else:
                dex_total += service_total
                dex_wallets[key] = {
                    "service": name,
                    "integration_id": integration_id,
                    "account_type": "spot",
                    "assets": service_assets,
                    "total_usd": service_total,
                }
            continue

        exchanges_count += 1

        if accounts:
            for acc in accounts:
                acc_type = acc.get("account_type")
                if acc_type == "spot":
                    spot_total += acc.get("total_usd", 0)
                    if acc.get("total_usd", 0) > 0 or acc.get("assets"):
                        spot_exchanges[key] = {**acc, "service": name, "integration_id": integration_id}
                elif acc_type == "futures":
                    futures_total += acc.get("total_usd", 0)
                    if acc.get("total_usd", 0) > 0 or acc.get("assets"):
                        futures_exchanges[key] = {**acc, "service": name, "integration_id": integration_id}
        else:
            spot_total += service_total
            if service_total > 0 or service_assets:
                spot_exchanges[key] = {
                    "service": name,
                    "integration_id": integration_id,
                    "account_type": "spot",
                    "assets": service_assets,
                    "total_usd": service_total,
                }

    return {
        "total": spot_total + futures_total + dex_total,
        "spot_total": spot_total,
        "futures_total": futures_total,
        "dex_total": dex_total,
        "exchanges_count": exchanges_count,
        "spot_exchanges": spot_exchanges,
        "futures_exchanges": futures_exchanges,
        "dex_wallets": dex_wallets,
    }
