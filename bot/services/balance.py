"""Balance data fetch and parsing helpers."""

from __future__ import annotations

from typing import Any
import time

from bot.api_client import api_client
from bot.config import settings

_balance_cache: dict[str, dict[str, Any]] = {}
_balance_cache_ts: dict[str, float] = {}
_BALANCE_CACHE_TTL_SECONDS = 5.0


def filter_assets(
    assets: list[dict[str, Any]], hide_small: bool
) -> list[dict[str, Any]]:
    if not hide_small:
        return assets
    return [
        asset
        for asset in assets
        if asset.get("value_usd", 0) >= settings.hide_small_balance_threshold
    ]


def invalidate_balance_cache(cache_key: str | None = None) -> None:
    global _balance_cache, _balance_cache_ts
    if cache_key is None:
        _balance_cache.clear()
        _balance_cache_ts.clear()
        return
    _balance_cache.pop(cache_key, None)
    _balance_cache_ts.pop(cache_key, None)


async def get_balance_data(force_update_cache: bool = False) -> dict[str, Any]:
    global _balance_cache, _balance_cache_ts
    cache_key = api_client.current_identity_cache_key()
    now = time.monotonic()
    is_fresh = now - _balance_cache_ts.get(cache_key, 0.0) < _BALANCE_CACHE_TTL_SECONDS
    if not force_update_cache and cache_key in _balance_cache and is_fresh:
        return _balance_cache[cache_key]
    try:
        _balance_cache[cache_key] = await api_client.get_balances(cached=True)
        _balance_cache_ts[cache_key] = now
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

    # Track which integration_ids already have a canonical DEX entry so legacy
    # duplicate rows (tron_ton_*, okx_wallet_*) are skipped.
    _dex_integration_ids_seen: set[int] = set()

    # IMPORTANT: legacy prefixes are substrings of some canonical ones
    # ("tron_ton_" starts with "tron_"), so we must check legacy FIRST.
    _LEGACY_PREFIXES = ("tron_ton_", "okx_wallet_")
    _CANONICAL_PREFIXES = ("evm_", "sol_", "tron_", "ton_", "sui_")

    def _classify(name: str) -> str:
        """Return 'legacy', 'canonical', or 'cex'."""
        # Check legacy first — tron_ton_ starts with tron_ so must precede it
        for p in _LEGACY_PREFIXES:
            if name.startswith(p):
                return "legacy"
        for p in _CANONICAL_PREFIXES:
            if name.startswith(p):
                return "canonical"
        return "cex"

    def _sort_key(s: dict[str, Any]) -> int:
        cls = _classify(s.get("service", ""))
        return 0 if cls == "canonical" else 1  # canonical first, legacy/cex last

    sorted_services = sorted(services, key=_sort_key)

    for svc in sorted_services:
        name = svc.get("service", "")
        integration_id = svc.get("integration_id")
        key = f"{name}#{integration_id}" if integration_id is not None else name
        accounts = svc.get("accounts", [])
        service_assets = svc.get("assets", [])
        service_total = svc.get("total_usd", 0)

        kind = _classify(name)
        is_dex = kind in ("canonical", "legacy")

        if is_dex:
            # Skip legacy rows when we already have a canonical entry for this
            # integration_id (avoids duplicate wallet display for tron_ton_ / okx_wallet_)
            if (
                kind == "legacy"
                and integration_id is not None
                and integration_id in _dex_integration_ids_seen
            ):
                continue

            if accounts:
                for acc in accounts:
                    if acc.get("account_type") == "spot":
                        dex_total += acc.get("total_usd", 0)
                        dex_wallets[key] = {
                            **acc,
                            "service": name,
                            "integration_id": integration_id,
                        }
            else:
                dex_total += service_total
                dex_wallets[key] = {
                    "service": name,
                    "integration_id": integration_id,
                    "account_type": "spot",
                    "assets": service_assets,
                    "total_usd": service_total,
                }

            if kind == "canonical" and integration_id is not None:
                _dex_integration_ids_seen.add(integration_id)
            continue

        exchanges_count += 1

        if accounts:
            for acc in accounts:
                acc_type = acc.get("account_type")
                if acc_type == "spot":
                    spot_total += acc.get("total_usd", 0)
                    if acc.get("total_usd", 0) > 0 or acc.get("assets"):
                        spot_exchanges[key] = {
                            **acc,
                            "service": name,
                            "integration_id": integration_id,
                        }
                elif acc_type == "futures":
                    futures_total += acc.get("total_usd", 0)
                    if acc.get("total_usd", 0) > 0 or acc.get("assets"):
                        futures_exchanges[key] = {
                            **acc,
                            "service": name,
                            "integration_id": integration_id,
                        }
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
