"""Bot-side tariff contract for plan limits and refresh policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TariffPlan:
    code: str
    name: str
    max_cex_accounts: int
    max_evm_wallets: int
    refresh_interval_seconds: int


TARIFF_PLANS: dict[str, TariffPlan] = {
    "free": TariffPlan(
        code="free",
        name="Free",
        max_cex_accounts=5,
        max_evm_wallets=1,
        refresh_interval_seconds=600,
    ),
    "low": TariffPlan(
        code="low",
        name="Low",
        max_cex_accounts=14,
        max_evm_wallets=3,
        refresh_interval_seconds=300,
    ),
    "medium": TariffPlan(
        code="medium",
        name="Medium",
        max_cex_accounts=28,
        max_evm_wallets=7,
        refresh_interval_seconds=120,
    ),
    "pro": TariffPlan(
        code="pro",
        name="Pro",
        max_cex_accounts=56,
        max_evm_wallets=15,
        refresh_interval_seconds=0,
    ),
}


def get_tariff_plan(code: str | None) -> TariffPlan:
    normalized = (code or "free").strip().lower()
    if normalized == "full":
        normalized = "pro"
    return TARIFF_PLANS.get(normalized, TARIFF_PLANS["free"])


def is_evm_chain(chain: str | None) -> bool:
    if not chain:
        return False
    normalized = chain.strip().lower()
    return normalized in {
        "eth",
        "ethereum",
        "arb",
        "arbitrum",
        "op",
        "optimism",
        "base",
        "bsc",
        "binance-smart-chain",
        "polygon",
        "matic",
        "avax",
        "avalanche",
        "linea",
        "scroll",
        "zksync",
        "blast",
        "mantle",
        "gnosis",
        "ftm",
        "fantom",
        "berachain",
    }
