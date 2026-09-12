"""Canonical CEX account labels and dashboard compatibility classification."""

from __future__ import annotations

from typing import Any


CANONICAL_ACCOUNT_TYPES = frozenset(
    {
        "spot",
        "margin",
        "funding",
        "usdt_futures",
        "coin_futures",
        "trading",
        "unified",
        "linear_futures",
        "inverse_futures",
        "main",
        "trade",
    }
)

_FUTURES_ACCOUNT_TYPES = frozenset(
    {"usdt_futures", "coin_futures", "linear_futures", "inverse_futures", "futures"}
)


def dashboard_account_bucket(account_type: str | None) -> str:
    """Map a source account label to the legacy dashboard spot/futures bucket."""
    normalized = str(account_type or "").strip().lower()
    return "futures" if normalized in _FUTURES_ACCOUNT_TYPES else "spot"


def account_counts_toward_total(account: Any) -> bool:
    """Mirrored unified-account views are display-only and must not be re-counted."""
    if isinstance(account, dict):
        return not bool(account.get("mirror_of"))
    return not bool(getattr(account, "mirror_of", None))