"""Supported exchange definitions for bot UI flows."""

from __future__ import annotations

SUPPORTED_CEX_EXCHANGES: list[tuple[str, str]] = [
    ("binance", "Binance"),
    ("bitget", "Bitget"),
    ("bybit", "Bybit"),
    ("gateio", "Gate.io"),
    ("htx", "HTX"),
    ("kucoin", "KuCoin"),
    ("mexc", "MEXC"),
    ("okx", "OKX"),
    ("bitmart", "BitMart"),
    ("poloniex", "Poloniex"),
    ("lbank", "LBank"),
    ("coinex", "CoinEx"),
    ("bingx", "BingX"),
    ("xt", "XT"),
]

SUPPORTED_CEX_EXCHANGE_CODES = {code for code, _ in SUPPORTED_CEX_EXCHANGES}
SUPPORTED_CEX_EXCHANGE_LABELS = {code: label for code, label in SUPPORTED_CEX_EXCHANGES}
