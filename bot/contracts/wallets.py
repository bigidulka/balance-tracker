"""Supported wallet provider definitions."""

from __future__ import annotations

SUPPORTED_WALLET_PROVIDERS: list[tuple[str, str]] = [
    ("debank", "DeBank (EVM)"),
]

SUPPORTED_WALLET_PROVIDER_CODES = {code for code, _ in SUPPORTED_WALLET_PROVIDERS}
SUPPORTED_WALLET_PROVIDER_LABELS = {
    code: label for code, label in SUPPORTED_WALLET_PROVIDERS
}
