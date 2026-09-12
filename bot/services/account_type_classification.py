"""CEX account labels used by the standalone Telegram bot image."""

from __future__ import annotations

from typing import Any


_FUTURES_ACCOUNT_TYPES = frozenset(
    {
        "usdt_futures",
        "coin_futures",
        "linear_futures",
        "inverse_futures",
        "futures",
    }
)


def dashboard_account_bucket(account_type: str | None) -> str:
    normalized = str(account_type or "").strip().lower()
    return "futures" if normalized in _FUTURES_ACCOUNT_TYPES else "spot"


def account_counts_toward_total(account: Any) -> bool:
    if isinstance(account, dict):
        return not bool(account.get("mirror_of"))
    return not bool(getattr(account, "mirror_of", None))