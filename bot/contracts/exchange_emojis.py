"""Premium emoji mapping for supported exchanges.

Source material was extracted from:
C:/Users/udinc/Documents/Playground/cmc-exchange-icons/out/telegram_exchange_map.csv

All IDs are now centralized in bot.ui_emoji.EXCHANGE_EMOJI_IDS.
This module re-exports them and provides the resolve helper.
"""

from __future__ import annotations

from bot.ui_emoji import EXCHANGE_EMOJI_IDS as SUPPORTED_EXCHANGE_EMOJI_IDS

_ALIASES: dict[str, str] = {
    "gate": "gateio",
    "gate.io": "gateio",
    "xt.com": "xt",
}


def resolve_exchange_emoji_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in SUPPORTED_EXCHANGE_EMOJI_IDS:
        return SUPPORTED_EXCHANGE_EMOJI_IDS[normalized]
    if normalized in _ALIASES:
        return SUPPORTED_EXCHANGE_EMOJI_IDS.get(_ALIASES[normalized])

    compact = normalized.replace(" ", "").replace(".", "")
    for code in SUPPORTED_EXCHANGE_EMOJI_IDS:
        if compact.startswith(code.replace(".", "")):
            return SUPPORTED_EXCHANGE_EMOJI_IDS[code]

    for alias, code in _ALIASES.items():
        if compact.startswith(alias.replace(".", "").replace(" ", "")):
            return SUPPORTED_EXCHANGE_EMOJI_IDS.get(code)

    return None
