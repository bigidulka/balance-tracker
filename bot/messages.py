"""Bot message builders and formatting helpers."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable

from aiogram.types import MessageEntity
from aiogram.utils.formatting import (
    Bold,
    CustomEmoji,
    Text,
    as_key_value,
    as_marked_list,
    as_section,
)

from bot.contracts.exchanges import SUPPORTED_CEX_EXCHANGES
from bot.contracts.wallets import SUPPORTED_WALLET_PROVIDERS
from bot.emoji_catalog import CHAIN_EMOJI_MAP
from bot.i18n import t
from bot.ui_emoji import UI_ICONS as EMOJI_IDS

ACCESS_DENIED = "Access denied"
ERROR_PREFIX = "Error"
NOOP = "noop"


def _emoji(symbol: str, key: str) -> CustomEmoji:
    return CustomEmoji(symbol, custom_emoji_id=EMOJI_IDS[key])


def _heading(symbol: str, key: str, title: str) -> Text:
    return Text(_emoji(symbol, key), " ", Bold(title))


def format_usd(value: float) -> str:
    if value >= 1000:
        return f"${value:,.2f}"
    return f"${value:.2f}"


def format_interval(seconds: int) -> str:
    seconds = max(int(seconds or 0), 0)
    if seconds == 0:
        return "∞"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _plain(fragment: Text | str) -> str:
    if isinstance(fragment, str):
        return fragment
    if isinstance(fragment, Text):
        return str(fragment.as_kwargs().get("text") or "")
    return str(fragment)


def _section_with_lines(title: str, lines: Iterable[Text | str]) -> Text:
    prepared = [_plain(line) for line in lines if _plain(line)]
    if not prepared:
        return as_section(Bold(title))
    return as_section(Bold(title), entity_safe_blockquote("\n".join(prepared)))


def _section_with_pairs(title: str, *pairs: tuple[str, str]) -> Text:
    return _section_with_lines(title, [f"{key}: {value}" for key, value in pairs])


def _status_blockquote(locale: str, *pairs: tuple[str, str]) -> Text:
    lines = [f"{key}: {value}" for key, value in pairs]
    return as_section(
        Bold(t(locale, "status")), entity_safe_blockquote("\n".join(lines))
    )


def _items_or_default(locale: str, rows: list[Text], empty_key: str) -> list[Text]:
    if rows:
        return rows
    return [Text(t(locale, empty_key))]


def dashboard_text(summary: dict[str, Any], *, locale: str = "ru", currency_label: str = "") -> Text:
    total = float(summary.get("total_usd") or 0.0)
    exchanges_count = int(summary.get("exchanges_count") or 0)
    spot_total = float(summary.get("spot_total") or 0.0)
    futures_total = float(summary.get("futures_total") or 0.0)
    dex_total = float(summary.get("dex_total") or 0.0)
    plan = summary.get("plan") or {}
    plan_name = str(plan.get("name") or plan.get("code") or t(locale, "unknown"))
    throttling = summary.get("throttling") or {}
    capabilities = summary.get("capabilities") or {}
    can_refresh = bool(
        capabilities.get("can_refresh", capabilities.get("refresh", True))
    )
    retry_after = int(throttling.get("retry_after_seconds") or 0)
    integrations = summary.get("integrations") or {}
    integrations_total = int(integrations.get("total") or 0)
    integrations_active = int(integrations.get("active") or 0)
    freshness_raw = str(summary.get("freshness") or t(locale, "no_data"))
    freshness = freshness_raw.replace("_", " ")
    refresh_state = (
        t(locale, "refresh_available")
        if can_refresh
        else t(locale, "refresh_cooldown", seconds=retry_after)
    )

    status_pairs = [
        (t(locale, "total"), format_usd(total)),
        (t(locale, "exchanges"), str(exchanges_count)),
        (t(locale, "spot"), format_usd(spot_total)),
        (t(locale, "futures"), format_usd(futures_total)),
        (t(locale, "dex_wallet"), format_usd(dex_total)),
        (
            t(locale, "integrations"),
            f"{integrations_active}/{integrations_total} {t(locale, 'active')}",
        ),
        (
            t(locale, "transactions"),
            t(locale, "transactions_coming_soon"),
        ),
        (t(locale, "plan"), plan_name),
        (t(locale, "refresh"), refresh_state),
        (t(locale, "freshness"), freshness),
    ]
    # Insert additional currency conversion after total if available.
    if currency_label:
        status_pairs.insert(1, (t(locale, "additional_currency"), currency_label))

    # Trader metrics section
    pnl_today_val = summary.get("pnl_today", summary.get("pnl_24h"))
    pnl_today_pct_val = summary.get("pnl_today_pct", summary.get("pnl_24h_pct"))
    pnl_7d_val = summary.get("pnl_7d")
    pnl_7d_pct_val = summary.get("pnl_7d_pct")
    pnl_30d_val = summary.get("pnl_30d")
    pnl_30d_pct_val = summary.get("pnl_30d_pct")

    trader_pairs: list[tuple[str, str]] = []

    def _append_pnl(label_key: str, value: Any, pct_value: Any) -> None:
        if value is None:
            return
        numeric = float(value)
        if abs(numeric) < 0.005:
            return
        sign = "+" if numeric >= 0 else ""
        pct = (
            f" ({sign}{float(pct_value):.1f}%)"
            if pct_value is not None
            else ""
        )
        trader_pairs.append((t(locale, label_key), f"{sign}{format_usd(numeric)}{pct}"))

    _append_pnl("pnl_today", pnl_today_val, pnl_today_pct_val)
    _append_pnl("pnl_7d", pnl_7d_val, pnl_7d_pct_val)
    _append_pnl("pnl_30d", pnl_30d_val, pnl_30d_pct_val)

    blocks: list[Any] = [
        _heading("📊", "dashboard", t(locale, "dashboard_title")),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            *status_pairs,
        ),
    ]
    if trader_pairs:
        blocks.extend([
            "\n\n",
            _section_with_pairs(
                t(locale, "trader_metrics"),
                *trader_pairs,
            ),
        ])

    return Text(*blocks)




def balance_overview_text(
    parsed: dict[str, Any], freshness: str, refresh_state: str, *, locale: str = "ru"
) -> Text:
    return Text(
        _heading("📊", "dashboard", t(locale, "dashboard_title")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "balance_overview_desc"))
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "total"), format_usd(float(parsed.get("total") or 0.0))),
            (t(locale, "exchanges"), str(int(parsed.get("exchanges_count") or 0))),
            (t(locale, "spot"), format_usd(float(parsed.get("spot_total") or 0.0))),
            (
                t(locale, "futures"),
                format_usd(float(parsed.get("futures_total") or 0.0)),
            ),
            (
                t(locale, "dex_wallet"),
                format_usd(float(parsed.get("dex_total") or 0.0)),
            ),
            (t(locale, "refresh"), refresh_state.replace("Refresh: ", "")),
            (t(locale, "freshness"), freshness),
        ),
    )


def section_title_text(
    title: str, total_usd: float, description: str, *, locale: str = "ru"
) -> Text:
    return Text(
        _heading("📊", "dashboard", title),
        "\n\n",
        as_section(Bold(t(locale, "description")), Text(description)),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"), (t(locale, "total"), format_usd(total_usd))
        ),
    )


def exchange_list_text(
    title: str,
    total_usd: float,
    description: str,
    lines: list[str],
    *,
    visible_count: int,
    total_count: int,
    page: int,
    total_pages: int,
    locale: str = "ru",
) -> Text:
    body = _items_or_default(locale, [Text(line) for line in lines], "no_exchanges")
    return Text(
        section_title_text(title, total_usd, description, locale=locale),
        "\n\n",
        _section_with_pairs(
            t(locale, "view"),
            (t(locale, "visible"), f"{visible_count}/{total_count}"),
            (t(locale, "page"), f"{page + 1}/{total_pages}"),
        ),
        "\n\n",
        _section_with_lines(t(locale, "items"), body),
    )


def exchange_detail_text(
    title: str,
    total_usd: float,
    description: str,
    assets: list[dict[str, Any]],
    has_more: bool,
    more_count: int,
    *,
    locale: str = "ru",
) -> Text:
    lines = [
        Text(
            f"{asset.get('coin', '?')}: {asset.get('amount', 0):.6g} ({format_usd(float(asset.get('value_usd', 0) or 0.0))})"
        )
        for asset in assets
    ]
    lines = _items_or_default(locale, lines, "no_assets")
    if has_more and more_count > 0:
        lines.append(Text(t(locale, "and_more", count=more_count)))

    return Text(
        section_title_text(title, total_usd, description, locale=locale),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"), (t(locale, "assets_shown"), str(len(assets)))
        ),
        "\n\n",
        _section_with_lines(t(locale, "items"), lines),
    )


def dex_text(
    total_usd: float,
    page: int,
    total_pages: int,
    assets: list[dict[str, Any]],
    *,
    locale: str = "ru",
) -> Text:
    rows = [
        Text(
            f"{asset.get('coin', '?')}: {asset.get('amount', 0):.6g} ({format_usd(float(asset.get('value_usd', 0) or 0.0))})"
        )
        for asset in assets
    ]
    return Text(
        _heading("👛", "dex", t(locale, "dex_wallet")),
        "\n\n",
        as_section(Bold(t(locale, "description")), Text(t(locale, "dex_description"))),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "total"), format_usd(total_usd)),
            (t(locale, "page"), f"{page + 1}/{total_pages}"),
            (t(locale, "assets_shown"), str(len(assets))),
        ),
        "\n\n",
        _section_with_lines(
            t(locale, "items"), _items_or_default(locale, rows, "no_assets")
        ),
    )


def dex_wallets_text(
    *,
    total_usd: float,
    lines: list[str],
    visible_count: int,
    total_count: int,
    page: int,
    total_pages: int,
    locale: str = "ru",
) -> Text:
    body = _items_or_default(locale, [Text(line) for line in lines], "no_integrations")
    return Text(
        _heading("👛", "dex", t(locale, "dex_wallet")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "dex_wallets_description"))
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "total"), format_usd(total_usd)),
            (t(locale, "wallets"), f"{visible_count}/{total_count}"),
            (t(locale, "page"), f"{page + 1}/{total_pages}"),
        ),
        "\n\n",
        _section_with_lines(t(locale, "items"), body),
    )


def _chain_emoji(chain_id: str) -> CustomEmoji:
    """Return a per-chain CustomEmoji, falling back to the generic link emoji."""
    emoji_id = CHAIN_EMOJI_MAP.get(chain_id.lower() if chain_id else "")
    if emoji_id:
        return CustomEmoji("🔗", custom_emoji_id=emoji_id)
    # Fallback: use the integrations emoji (📦-ish) if no chain-specific one
    return CustomEmoji("🔗", custom_emoji_id=EMOJI_IDS["integrations"])


def dex_wallet_detail_text(
    *,
    wallet_label: str,
    address: str = "",
    total_usd: float,
    assets: list[dict[str, Any]],
    has_more: bool,
    more_count: int,
    page: int,
    total_pages: int,
    total_asset_count: int = 0,
    chains: list[dict[str, Any]] | None = None,
    portfolio_tokens: list[dict[str, Any]] | None = None,
    locale: str = "ru",
) -> Text:
    token_count = total_asset_count or len(assets)

    # --- Title: 👛 short label ---
    blocks: list[Any] = [
        _heading("👛", "dex", wallet_label),
    ]

    # --- Full address as copyable code entity ---
    if address:
        addr_utf16_len = len(address.encode("utf-16-le")) // 2
        addr_entity = MessageEntity(type="code", offset=0, length=addr_utf16_len)
        blocks += ["\n", Text.from_entities(address, [addr_entity])]

    # --- Stats block ---
    blocks += [
        "\n\n",
        _emoji("🪙", "spot"),
        " ",
        Bold(f"{t(locale, 'total')}:"),
        Text(f" {format_usd(total_usd)}"),
        "\n",
        _emoji("👛", "wallet"),
        " ",
        Bold(f"{t(locale, 'tokens')}:"),
        Text(f" {token_count}"),
    ]
    if total_pages > 1:
        blocks += [
            "\n",
            _emoji("📊", "dashboard"),
            " ",
            Bold(f"{t(locale, 'page')}:"),
            Text(f" {page + 1}/{total_pages}"),
        ]

    # --- Chains section (from portfolio API) ---
    chain_list = chains or []
    if chain_list:
        blocks += ["\n\n", _emoji("📦", "integrations"), " ", Bold(t(locale, "chains"))]
        for chain in chain_list[:7]:
            chain_id = str(chain.get("id") or "")
            chain_name = str(chain.get("name") or chain_id or "?")
            chain_usd = float(chain.get("totalUsd") or 0.0)
            chain_tokens = int(chain.get("tokenCount") or 0)
            blocks += [
                "\n",
                _chain_emoji(chain_id),
                Text(f" {chain_name} · {format_usd(chain_usd)} · {chain_tokens} ток."),
            ]

    # --- Assets section ---
    # Prefer portfolio_tokens (have full names + chain), fall back to bare assets
    token_list = portfolio_tokens or []
    blocks += ["\n\n", _emoji("📈", "futures"), " ", Bold(t(locale, "tokens"))]

    if token_list:
        for tok in token_list[:20]:
            symbol = str(tok.get("symbol") or "?")
            name = str(tok.get("name") or "")
            chain_id = str(tok.get("chain") or "")
            usd = float(tok.get("amountUsd") or 0.0)
            label_part = f"{symbol} ({name})" if name and name != symbol else symbol
            blocks += [
                "\n",
                _chain_emoji(chain_id),
                Text(f" {label_part} · {format_usd(usd)}"),
            ]
        if has_more and more_count > 0:
            blocks += ["\n", Text(t(locale, "and_more", count=more_count))]
    else:
        # Fallback: use bare assets from balance API
        asset_rows: list[Any] = []
        for asset in assets:
            symbol = str(asset.get("coin", "?"))
            usd = float(asset.get("value_usd", 0) or 0.0)
            asset_rows += [
                "\n",
                CustomEmoji("🔗", custom_emoji_id=EMOJI_IDS["integrations"]),
                Text(f" {symbol} · {format_usd(usd)}"),
            ]
        if asset_rows:
            blocks += asset_rows
        else:
            blocks += ["\n", Text(t(locale, "no_assets"))]
        if has_more and more_count > 0:
            blocks += ["\n", Text(t(locale, "and_more", count=more_count))]

    return Text(*blocks)


def transactions_text(
    tx_type: str,
    tx_status: str,
    tx_since_hours: int,
    rows: list[dict[str, Any]],
    page: int,
    *,
    locale: str = "ru",
) -> Text:
    items: list[Text] = []
    for tx in rows:
        row_type = str(tx.get("tx_type", "unknown"))
        direction = (
            t(locale, "deposit") if row_type == "deposit" else t(locale, "withdrawal")
        )
        amount = float(tx.get("amount", 0) or 0)
        currency = tx.get("currency", "?")
        service = str(tx.get("service", "unknown")).upper()
        status = t(locale, str(tx.get("status", "pending")).lower())
        items.append(
            Text(f"{service} • {direction} • {amount:.6g} {currency} • {status}")
        )

    return Text(
        _heading("🧾", "transactions", t(locale, "transactions")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "transactions_description"))
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "type"), t(locale, tx_type)),
            (t(locale, "state"), t(locale, tx_status)),
            (t(locale, "window"), f"{tx_since_hours}h"),
            (t(locale, "page"), str(page + 1)),
            (t(locale, "rows"), str(len(rows))),
        ),
        "\n\n",
        _section_with_lines(
            t(locale, "items"), _items_or_default(locale, items, "no_transactions")
        ),
    )


def transaction_sources_text(
    *,
    lines: list[str],
    visible_count: int,
    total_count: int,
    page: int,
    total_pages: int,
    locale: str = "ru",
) -> Text:
    body = _items_or_default(locale, [Text(line) for line in lines], "no_integrations")
    return Text(
        _heading("🧾", "transactions", t(locale, "transactions")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")),
            Text(t(locale, "transaction_source_description")),
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "sources"), f"{visible_count}/{total_count}"),
            (t(locale, "page"), f"{page + 1}/{total_pages}"),
        ),
        "\n\n",
        _section_with_lines(t(locale, "items"), body),
    )


def transaction_detail_text(
    *,
    source_label: str,
    rows: list[dict[str, Any]],
    page: int,
    locale: str = "ru",
) -> Text:
    items: list[Text] = []
    for tx in rows:
        row_type = str(tx.get("tx_type", "unknown")).lower()
        direction = (
            t(locale, "deposit") if row_type == "deposit" else t(locale, "withdrawal")
        )
        amount = float(tx.get("amount", 0) or 0)
        currency = str(tx.get("currency", "?"))
        status = t(locale, str(tx.get("status", "pending")).lower())
        network = str(tx.get("network") or tx.get("chain") or "").strip()
        tx_time = tx.get("tx_timestamp") or tx.get("created_at")
        stamp = str(tx_time)[:16].replace("T", " ") if tx_time else "-"
        parts = [stamp, direction, f"{amount:.6g} {currency}", status]
        if network:
            parts.append(network)
        items.append(Text(" • ".join(parts)))

    return Text(
        _heading("🧾", "transactions", f"{t(locale, 'transactions')} • {source_label}"),
        "\n\n",
        as_section(
            Bold(t(locale, "description")),
            Text(t(locale, "transaction_detail_description")),
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "source"), source_label),
            (t(locale, "page"), str(page + 1)),
            (t(locale, "rows"), str(len(rows))),
        ),
        "\n\n",
        _section_with_lines(
            t(locale, "items"), _items_or_default(locale, items, "no_transactions")
        ),
    )


def _code_line(prefix: str, value: str) -> Text:
    clean = str(value or "").strip()
    if not clean:
        return Text(prefix)
    utf16_length = len(clean.encode("utf-16-le")) // 2
    entity = MessageEntity(type="code", offset=0, length=utf16_length)
    return Text(prefix, Text.from_entities(clean, [entity]))


def _provider_display(value: str) -> str:
    raw = str(value or "").strip()
    return _PROVIDER_DISPLAY.get(raw, raw)


def _integration_type_label(item: dict[str, Any]) -> str:
    """Human-readable type label for integration list body.

    CEX → exchange label ("Binance", "OKX", …) — already in display_name
    DEX → chain prefix ("EVM", "SOL", "TRX", "TON", "SUI")
    """
    kind = str(item.get("kind") or "").strip().lower()
    if kind == "cex":
        return "CEX"
    # DEX: derive from chain first, then provider
    chain = str(item.get("chain") or "").strip().lower()
    _CHAIN_TYPE: dict[str, str] = {
        "ethereum": "EVM",
        "bsc": "EVM",
        "polygon": "EVM",
        "arbitrum": "EVM",
        "optimism": "EVM",
        "base": "EVM",
        "avalanche": "EVM",
        "fantom": "EVM",
        "gnosis": "EVM",
        "zksync": "EVM",
        "linea": "EVM",
        "scroll": "EVM",
        "mantle": "EVM",
        "blast": "EVM",
        "taiko": "EVM",
        "solana": "SOL",
        "tron": "TRX",
        "ton": "TON",
        "sui": "SUI",
    }
    if chain and chain in _CHAIN_TYPE:
        return _CHAIN_TYPE[chain]
    # Fallback to provider mapping
    provider = str(item.get("provider") or "").strip().lower()
    return _PROVIDER_DISPLAY.get(provider, provider.upper() if provider else "DEX")


def integrations_text(
    items: list[dict[str, Any]],
    *,
    page: int = 0,
    per_page: int = 8,
    cex_used: int = 0,
    cex_limit: int = 0,
    evm_used: int = 0,
    evm_limit: int = 0,
    locale: str = "ru",
) -> Text:
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]
    total = len(items)
    total_pages = max(1, math.ceil(total / per_page)) if total else 1
    lines = [
        Text(
            f"{(item.get('display_name') or item.get('name') or ('integration-' + str(item.get('id', '?'))))} [{_integration_type_label(item)}]"
            f" - {t(locale, 'active') if item.get('is_active') else t(locale, 'inactive')}"
        )
        for item in page_items
    ]
    status_pairs: list[tuple[str, str]] = [
        (t(locale, "total"), str(total)),
        (t(locale, "cex_accounts"), f"{cex_used}/{cex_limit}"),
        (t(locale, "wallets"), f"{evm_used}/{evm_limit}"),
    ]
    if total_pages > 1:
        status_pairs.append((t(locale, "page"), f"{page + 1}/{total_pages}"))
    return Text(
        _heading("📦", "integrations", t(locale, "integrations")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "integrations_description"))
        ),
        "\n\n",
        _section_with_pairs(t(locale, "status"), *status_pairs),
        "\n\n",
        _section_with_lines(
            t(locale, "items"), _items_or_default(locale, lines, "no_integrations")
        ),
    )


def integration_exchange_picker_text(*, locale: str = "ru") -> Text:
    lines = [Text(f"{label} ({code})") for code, label in SUPPORTED_CEX_EXCHANGES]
    return Text(
        _heading("📦", "integrations", t(locale, "add_exchange")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")),
            Text(t(locale, "integration_picker_description")),
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "supported"), str(len(SUPPORTED_CEX_EXCHANGES))),
        ),
        "\n\n",
        _section_with_lines(t(locale, "items"), lines),
    )


def integration_wallet_picker_text(*, locale: str = "ru") -> Text:
    lines = [Text(label) for _code, label in SUPPORTED_WALLET_PROVIDERS]
    return Text(
        _heading("📦", "integrations", t(locale, "add_wallet_provider")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "wallet_picker_description"))
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "supported"), str(len(SUPPORTED_WALLET_PROVIDERS))),
        ),
        "\n\n",
        _section_with_lines(t(locale, "items"), lines),
    )


_PROVIDER_DISPLAY: dict[str, str] = {
    # DEX wallet providers → human-readable label
    "debank": "EVM",
    "okx_wallet": "SOL",
    "tron_ton": "TRON/TON",
    "sui": "SUI",
    # CEX
    "ccxt": "CEX",
    "cryptobot": "CryptoBot",
}


_CHAIN_DISPLAY: dict[str, str] = {
    "ethereum": "evm",
    "bsc": "evm",
    "polygon": "evm",
    "arbitrum": "evm",
    "optimism": "evm",
    "avalanche": "evm",
    "base": "evm",
    "fantom": "evm",
    "gnosis": "evm",
    "zksync": "evm",
    "linea": "evm",
    "solana": "solana",
    "tron": "tron",
    "ton": "ton",
}


def integration_detail_text(
    item: dict[str, Any], job_id: int | None = None, *, locale: str = "ru"
) -> Text:
    integration_id = int(item.get("id", -1))
    raw_provider = str(item.get("provider", "unknown"))
    display_provider = _PROVIDER_DISPLAY.get(raw_provider, raw_provider)
    raw_chain = str(item.get("chain", ""))
    display_chain = _CHAIN_DISPLAY.get(raw_chain, raw_chain) if raw_chain else ""

    identity_lines: list[Text] = []
    if item.get("exchange_code"):
        identity_lines.append(
            Text(f"{t(locale, 'exchanges')}: {item.get('exchange_code')}")
        )
    if item.get("account_ref"):
        identity_lines.append(
            Text(f"{t(locale, 'account')}: {item.get('account_ref')}")
        )
    if item.get("wallet_address"):
        identity_lines.append(
            _code_line(f"{t(locale, 'address')}: ", str(item.get("wallet_address")))
        )
    if display_chain:
        identity_lines.append(Text(f"{t(locale, 'chain')}: {display_chain}"))

    status_pairs: list[tuple[str, str]] = [
        (t(locale, "id"), str(integration_id)),
        (
            t(locale, "name"),
            str(
                item.get("display_name")
                or item.get("name", f"integration-{integration_id}")
            ),
        ),
        (t(locale, "provider"), display_provider),
        (t(locale, "kind"), str(item.get("kind", "unknown"))),
        (
            t(locale, "state"),
            t(locale, "active") if item.get("is_active") else t(locale, "inactive"),
        ),
    ]
    if job_id is not None:
        status_pairs.append((t(locale, "refresh"), str(job_id)))

    blocks: list[Any] = [
        _heading("📦", "integrations", t(locale, "integration_detail_title")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")),
            Text(t(locale, "integration_detail_description")),
        ),
        "\n\n",
        _section_with_pairs(t(locale, "status"), *status_pairs),
    ]
    if identity_lines:
        blocks.extend(
            ["\n\n", as_section(Bold(t(locale, "identity")), *identity_lines)]
        )
    return Text(*blocks)


def settings_text(
    hide_small: bool,
    threshold: float,
    language: str,
    *,
    locale: str = "ru",
    display_currency: str = "USD",
) -> Text:
    from bot.services.fx_rates import currency_label as _fx_label
    currency_display = _fx_label(display_currency, locale)
    return Text(
        _heading("⚙️", "settings", t(locale, "settings")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "settings_description"))
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "language"), t(language, "language_name")),
            (t(locale, "display_currency"), currency_display),
            (
                t(locale, "hide_small_balances"),
                t(locale, "on") if hide_small else t(locale, "off"),
            ),
            (t(locale, "threshold"), format_usd(threshold)),
        ),
    )



def plan_text(
    *,
    plan_code: str,
    plan_name: str,
    cex_used: int,
    cex_limit: int,
    evm_used: int,
    evm_limit: int,
    refresh_interval_seconds: int,
    can_refresh: bool,
    retry_after_seconds: int,
    wallet_balance_usd: float = 0.0,
    plans: list[dict[str, Any]] | None = None,
    reason: str | None = None,
    locale: str = "ru",
) -> Text:
    refresh_value = format_interval(refresh_interval_seconds)
    refresh_state = (
        t(locale, "refresh_available")
        if can_refresh
        else t(locale, "refresh_cooldown", seconds=retry_after_seconds)
    )
    if refresh_interval_seconds <= 0:
        refresh_line = f"{t(locale, 'refresh_interval')}: {t(locale, 'no_cooldown')}"
    else:
        refresh_line = (
            f"{t(locale, 'refresh_interval')}: {t(locale, 'every')} {refresh_value}"
        )
    status_lines = [
        f"{t(locale, 'current_plan')}: {plan_name} ({plan_code.upper()})",
        f"{t(locale, 'cex_accounts')}: {cex_used}/{cex_limit}",
        f"{t(locale, 'wallets')}: {evm_used}/{evm_limit}",
        refresh_line,
        f"{t(locale, 'refresh')}: {refresh_state}",
        f"{t(locale, 'balance_wallet')}: {format_usd(wallet_balance_usd)}",
    ]
    blocks: list[Any] = [
        _heading("🪙", "plan", t(locale, "tariff")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "tariff_description"))
        ),
        "\n\n",
        _section_with_lines(t(locale, "status"), [Text(line) for line in status_lines]),
    ]
    plan_rows: list[Text] = []
    for item in plans or []:
        if not isinstance(item, dict):
            continue
        item_code = str(item.get("code") or "").strip().lower()
        if not item_code:
            continue
        item_name = str(item.get("name") or item_code.upper())
        item_policy = item.get("policy") if isinstance(item.get("policy"), dict) else {}
        item_limits = (
            item_policy.get("limits")
            if isinstance(item_policy.get("limits"), dict)
            else {}
        )
        item_background = (
            item_policy.get("background")
            if isinstance(item_policy.get("background"), dict)
            else {}
        )
        item_cex_limit = int(item_limits.get("max_cex_accounts") or 0)
        item_wallet_limit = int(
            item_limits.get("max_wallets") or item_limits.get("max_evm_wallets") or 0
        )
        item_refresh = format_interval(
            int(
                item_background.get("refresh_interval_seconds")
                or item_limits.get("min_refresh_interval_seconds")
                or 0
            )
        )
        item_price = float(item.get("price_monthly") or 0.0)
        suffix = (
            t(locale, "current_plan_short")
            if item_code == plan_code
            else format_usd(item_price)
        )
        plan_rows.append(
            Text(
                f"{item_name} ({item_code.upper()}) · {item_cex_limit} CEX · {item_wallet_limit} {t(locale, 'wallets')} · {item_refresh} · {suffix}"
            )
        )
    if plan_rows:
        blocks.extend(
            ["\n\n", _section_with_lines(t(locale, "available_plans"), plan_rows)]
        )
    if reason:
        blocks.extend(
            [
                "\n\n",
                as_section(
                    Bold(t(locale, "blocked")),
                    entity_safe_blockquote(f"{reason}"),
                ),
            ]
        )
    blocks.extend(
        [
            "\n\n",
            as_section(
                Bold(t(locale, "actions")),
                as_marked_list(
                    t(locale, "select_plan"),
                    t(locale, "top_up_balance"),
                    t(locale, "apply_promo"),
                    t(locale, "back"),
                ),
            ),
        ]
    )
    return Text(*blocks)


def gated_feature_text(reason: str, *, locale: str = "ru") -> Text:
    return Text(
        _heading("⚠️", "warn", t(locale, "blocked")),
        "\n\n",
        as_section(Bold(t(locale, "status")), entity_safe_blockquote(reason)),
    )


def loading_text(label: str = "Loading...", *, locale: str = "ru") -> Text:
    return Text(
        _emoji("🔄", "loading"), " ", Bold(label if label else t(locale, "refresh"))
    )


def access_denied_text(error_text: str, *, locale: str = "ru") -> Text:
    return Text(
        _emoji("❌", "error"),
        " ",
        Bold(t(locale, "access_denied")),
        "\n\n",
        Text(error_text),
    )


def error_text(exc: Exception | str, *, locale: str = "ru") -> Text:
    return Text(
        _emoji("❌", "error"), " ", Bold(t(locale, "error")), "\n", Text(str(exc))
    )


def refresh_unavailable_text(retry_after: int = 0, *, locale: str = "ru") -> Text:
    text = t(locale, "refresh_unavailable_text")
    if retry_after > 0:
        text = t(locale, "refresh_rate_limited_text", seconds=retry_after)
    return Text(
        _emoji("📣", "warn"),
        " ",
        Bold(t(locale, "refresh_unavailable_title")),
        "\n",
        Text(text),
    )


def dex_unavailable_text(*, locale: str = "ru") -> Text:
    return Text(
        _emoji("📣", "warn"),
        " ",
        Bold(t(locale, "dex_unavailable_title")),
        "\n",
        Text(t(locale, "dex_unavailable_text")),
    )


def integration_not_found_text(*, locale: str = "ru") -> Text:
    return Text(_emoji("❌", "error"), " ", Bold(t(locale, "integration_not_found")))


def input_waiting_text(
    kind: str, error_text: str | None = None, *, locale: str = "ru"
) -> Text:
    label = (
        t(locale, f"input_{kind}") if f"input_{kind}" else t(locale, "input_unknown")
    )
    if label == f"input_{kind}":
        label = t(locale, "input_unknown")
    blocks: list[Any] = [
        _heading("✍", "input", t(locale, "input_mode")),
        "\n\n",
        as_section(Bold(t(locale, "description")), Text(label)),
    ]
    if error_text:
        blocks.extend(
            [
                "\n\n",
                as_section(
                    Bold(t(locale, "error")), entity_safe_blockquote(error_text)
                ),
            ]
        )
    blocks.extend(
        [
            "\n\n",
            as_section(
                Bold(t(locale, "actions")),
                as_marked_list(
                    t(locale, "input_send_value"), t(locale, "input_back_exit")
                ),
            ),
        ]
    )
    return Text(*blocks)


def payments_text(
    *,
    balance_usd: float,
    invoices: list[dict[str, Any]],
    active_invoice: dict[str, Any] | None,
    locale: str = "ru",
) -> Text:
    invoice_lines: list[Text] = []
    for item in invoices[:5]:
        invoice_lines.append(
            Text(
                f"#{item.get('id', '?')}: {format_usd(float(item.get('amount', 0) or 0.0))} - "
                f"{str(item.get('status', 'pending')).upper()}"
            )
        )
    blocks: list[Any] = [
        _heading("💰", "wallet", t(locale, "top_up_balance")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "payments_description"))
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "balance_wallet"), format_usd(balance_usd)),
            (t(locale, "invoices"), str(len(invoices))),
        ),
        "\n\n",
        _section_with_pairs(
            "CryptoBot" if locale == "en" else "CryptoBot",
            (
                "User wallet" if locale == "en" else "Кошелек пользователя",
                "Open CryptoBot directly" if locale == "en" else "Открыть CryptoBot напрямую",
            ),
            (
                "App wallet top-up" if locale == "en" else "Пополнение кошелька приложения",
                "Create invoice for app balance" if locale == "en" else "Создать чек для баланса приложения",
            ),
        ),
    ]
    if active_invoice:
        blocks.extend(
            [
                "\n\n",
                _section_with_lines(
                    t(locale, "active_invoice"),
                    [
                        Text(f"ID: {active_invoice.get('id', '?')}"),
                        Text(
                            f"{t(locale, 'state')}: {active_invoice.get('status', 'pending')}"
                        ),
                        Text(
                            f"{t(locale, 'amount')}: {format_usd(float(active_invoice.get('amount', 0) or 0.0))}"
                        ),
                    ],
                ),
            ]
        )
    blocks.extend(
        [
            "\n\n",
            _section_with_lines(
                t(locale, "items"),
                _items_or_default(locale, invoice_lines, "no_invoices"),
            ),
        ]
    )
    return Text(*blocks)


def admin_panel_text(*, locale: str = "ru") -> Text:
    return Text(
        _heading("⚙️", "settings", t(locale, "admin_panel")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "admin_description"))
        ),
        "\n\n",
        as_section(
            Bold(t(locale, "actions")),
            as_marked_list(t(locale, "users"), t(locale, "back")),
        ),
    )


def admin_users_text(
    *,
    items: list[dict[str, Any]],
    total: int,
    page: int,
    page_size: int,
    locale: str = "ru",
) -> Text:
    def _label(item: dict[str, Any]) -> str:
        username = str(item.get("telegram_username") or "").strip()
        if username:
            return f"@{username}"
        full_name = str(
            item.get("telegram_full_name") or item.get("full_name") or ""
        ).strip()
        if full_name:
            return full_name
        return str(item.get("email") or f"user-{item.get('user_id', '?')}")

    rows = [
        Text(
            f"{_label(item)} - {item.get('plan_name', 'Free')} - "
            f"{format_usd(float(item.get('balance_usd', 0) or 0.0))}"
        )
        for item in items
    ]
    return Text(
        _heading("👤", "settings", t(locale, "users")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "admin_users_description"))
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "total"), str(total)),
            (t(locale, "page"), f"{page + 1}"),
            (t(locale, "rows"), str(page_size)),
        ),
        "\n\n",
        _section_with_lines(
            t(locale, "items"), _items_or_default(locale, rows, "no_users")
        ),
    )


def admin_user_detail_text(item: dict[str, Any], *, locale: str = "ru") -> Text:
    integrations = (
        item.get("integrations") if isinstance(item.get("integrations"), list) else []
    )
    integration_rows = [
        Text(
            f"{integration.get('name', 'integration')} [{integration.get('provider', 'unknown')}] "
            f"- {t(locale, 'on') if integration.get('is_active') else t(locale, 'off')}"
        )
        for integration in integrations
    ]
    return Text(
        _heading("👤", "settings", t(locale, "user_details")),
        "\n\n",
        as_section(
            Bold(t(locale, "description")),
            Text(t(locale, "admin_user_detail_description")),
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (
                t(locale, "name"),
                str(
                    item.get("telegram_full_name")
                    or item.get("full_name")
                    or item.get("telegram_username")
                    or item.get("email", "unknown")
                ),
            ),
            (t(locale, "email"), str(item.get("email", "unknown"))),
            (t(locale, "organization"), str(item.get("organization_name", "unknown"))),
            (t(locale, "role"), str(item.get("role", "member"))),
            (t(locale, "plan"), str(item.get("plan_name", "Free"))),
            (
                t(locale, "balance_wallet"),
                format_usd(float(item.get("balance_usd", 0) or 0.0)),
            ),
            (t(locale, "integrations"), str(item.get("active_integrations", 0))),
        ),
        "\n\n",
        _section_with_lines(
            t(locale, "items"),
            _items_or_default(locale, integration_rows, "no_integrations"),
        ),
    )


def format_timestamp(data: dict[str, Any], *, locale: str = "ru") -> str:
    services = data.get("services", [])
    if not services:
        return t(locale, "no_data")
    latest_time = None
    for svc in services:
        updated_at = svc.get("updated_at")
        if not updated_at:
            continue
        try:
            dt = (
                datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if isinstance(updated_at, str)
                else updated_at
            )
            if latest_time is None or dt > latest_time:
                latest_time = dt
        except Exception:
            continue
    if latest_time is None:
        return t(locale, "unknown")
    now = datetime.now(timezone.utc)
    if latest_time.tzinfo is None:
        latest_time = latest_time.replace(tzinfo=timezone.utc)
    minutes = int((now - latest_time).total_seconds() / 60)
    if minutes < 1:
        return t(locale, "updated_just_now")
    return t(locale, "updated_minutes_ago", minutes=minutes)


def format_refresh_state(
    capabilities: dict[str, Any], throttling: dict[str, Any], *, locale: str = "ru"
) -> str:
    can_refresh = bool(
        capabilities.get("can_refresh", capabilities.get("refresh", True))
    )
    if can_refresh:
        return f"{t(locale, 'refresh')}: {t(locale, 'refresh_available')}"
    retry_after = int(throttling.get("retry_after_seconds") or 0)
    if retry_after > 0:
        return f"{t(locale, 'refresh')}: {t(locale, 'refresh_cooldown', seconds=retry_after)}"
    return f"{t(locale, 'refresh')}: {t(locale, 'refresh_unavailable')}"


def as_edit_kwargs(text: Text, reply_markup: Any) -> dict[str, Any]:
    kwargs = text.as_kwargs()
    kwargs["reply_markup"] = reply_markup
    return kwargs


def entity_safe_blockquote(text: str) -> Text:
    clean = text or ""
    if not clean:
        return Text("")
    utf16_length = len(clean.encode("utf-16-le")) // 2
    entity = MessageEntity(type="blockquote", offset=0, length=utf16_length)
    return Text.from_entities(clean, [entity])


def preserve_text_entities(
    original_text: str | None, entities: list[MessageEntity] | None
) -> Text:
    return Text.from_entities(original_text or "", entities or [])


def dex_portfolio_text(
    *,
    wallet_label: str,
    total_usd: float,
    total_tokens: int,
    chains: list[dict],
    tokens: list[dict],
    page: int,
    has_prev: bool,
    has_next: bool,
    locale: str = "ru",
) -> Text:
    chain_rows: list[Text] = [
        Text(
            f"{c.get('chainName') or c.get('chain') or '?'}: {format_usd(float(c.get('usdValue') or c.get('usd_value') or 0))}"
        )
        for c in (chains or [])
    ]
    token_rows: list[Text] = []
    for tok in tokens or []:
        symbol = str(tok.get("symbol") or "?")
        chain_name = str(tok.get("chainName") or tok.get("chain") or "?")
        amount = float(tok.get("amount") or 0)
        usd = float(tok.get("amountUsd") or tok.get("amount_usd") or 0)
        is_scam = bool(tok.get("isScam") or tok.get("is_scam"))
        prefix = "\u26a0 " if is_scam else ""
        token_rows.append(
            Text(f"{prefix}{symbol} ({chain_name}): {amount:g} (~{format_usd(usd)})")
        )

    page_label = f"{t(locale, 'page')} {page + 1}"
    if has_prev or has_next:
        page_label += " ..."

    return Text(
        _heading(
            "\U0001f45b",
            "wallet",
            f"dex \u2022 {t(locale, 'dex_portfolio')} \u2022 {wallet_label}",
        ),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "dex_portfolio_description"))
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "wallet_label"), wallet_label),
            (t(locale, "total"), format_usd(total_usd)),
            (t(locale, "chains"), str(len(chains or []))),
            (t(locale, "token_count"), str(total_tokens)),
            (t(locale, "page"), page_label),
        ),
        "\n\n",
        _section_with_lines(
            t(locale, "chains"), chain_rows or [Text(t(locale, "no_data"))]
        ),
        "\n\n",
        _section_with_lines(
            t(locale, "tokens"), _items_or_default(locale, token_rows, "no_tokens")
        ),
    )


def dex_tx_text(
    *,
    wallet_label: str,
    items: list[dict],
    cursor: int,
    next_cursor: int | None,
    hide_scam: bool,
    locale: str = "ru",
) -> Text:
    tx_rows: list[Text] = []
    for tx in items or []:
        chain_name = str(tx.get("chainName") or tx.get("chain") or "?")
        tx_name = str(tx.get("txName") or "?")
        time_at = tx.get("timeAt")
        try:
            dt_str = (
                datetime.fromtimestamp(int(time_at), tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M"
                )
                if time_at
                else "?"
            )
        except Exception:
            dt_str = str(time_at or "?")
        received_usd = float(tx.get("receivedUsd") or tx.get("received_usd") or 0)
        sent_usd = float(tx.get("sentUsd") or tx.get("sent_usd") or 0)
        net = received_usd - sent_usd
        net_str = f"+{format_usd(net)}" if net >= 0 else format_usd(net)
        lines = [f"{dt_str} \u2022 {tx_name} \u2022 {chain_name} \u2022 {net_str}"]
        for r in tx.get("receives") or []:
            symbol = str(r.get("symbol") or "?")
            amount = float(r.get("amount") or 0)
            lines.append(f"  +{amount:g} {symbol}")
        for s in tx.get("sends") or []:
            symbol = str(s.get("symbol") or "?")
            amount = float(s.get("amount") or 0)
            lines.append(f"  -{amount:g} {symbol}")
        tx_rows.append(Text("\n".join(lines)))

    hide_scam_label = t(locale, "on") if hide_scam else t(locale, "off")

    return Text(
        _heading(
            "\U0001f45b",
            "wallet",
            f"dex \u2022 {t(locale, 'dex_transactions')} \u2022 {wallet_label}",
        ),
        "\n\n",
        as_section(
            Bold(t(locale, "description")), Text(t(locale, "dex_tx_description"))
        ),
        "\n\n",
        _section_with_pairs(
            t(locale, "status"),
            (t(locale, "wallet_label"), wallet_label),
            (t(locale, "hide_scam"), hide_scam_label),
            (t(locale, "items"), str(len(items or []))),
        ),
        "\n\n",
        _section_with_lines(
            t(locale, "transactions"),
            _items_or_default(locale, tx_rows, "no_dex_transactions"),
        ),
    )
