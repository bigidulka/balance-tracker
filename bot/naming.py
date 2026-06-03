"""Unified integration naming — single source of truth.

Rule:
  CEX:  {ExchangeDisplayName} {Label}           → "Bybit main", "Binance trading"
  DEX:  {NetworkName} {ShortAddress}            → "Ethereum 0xf9…4cd8", "TON UQDa…c0z"

If the user set a custom name (integration.name not matching auto-pattern),
that name wins over the formula.

For buttons on Spot/Futures/DEX-wallet tabs, append balance:
  "Bybit main · $292.45"
  "Ethereum 0xf9…4cd8 · $2,235"
"""

from __future__ import annotations

from typing import Any

from bot.contracts.exchanges import SUPPORTED_CEX_EXCHANGE_LABELS

# ── Network display names ──────────────────────────────────────────────

_NETWORK_NAMES: dict[str, str] = {
    "ethereum": "ETH",
    "arbitrum": "Arbitrum",
    "optimism": "Optimism",
    "base": "Base",
    "bsc": "BSC",
    "polygon": "Polygon",
    "avalanche": "Avalanche",
    "fantom": "Fantom",
    "gnosis": "Gnosis",
    "zksync": "zkSync",
    "linea": "Linea",
    "scroll": "Scroll",
    "mantle": "Mantle",
    "blast": "Blast",
    "solana": "SOL",
    "tron": "TRX",
    "ton": "TON",
    "sui": "SUI",
    "evm": "ETH",  # fallback when chain not specified
}

# Service key prefixes → longest match first
_WALLET_PREFIXES: tuple[str, ...] = (
    "okx_wallet_",
    "tron_ton_",
    "evm_",
    "sol_",
    "tron_",
    "ton_",
    "sui_",
)


# ── Address abbreviation ────────────────────────────────────────────────

def short_addr(address: str) -> str:
    """Abbreviate a wallet address for display.

    0x1234567890abcdef1234567890abcdef12345678 → 0x1234…5678
    TAAJTevA5fFsuizex43KQX2GnFCrDYoHVH          → TAAJTev…HVH
    UQDa41Hlmn55k…8c0Z                           → UQDa4…8c0Z  (case-preserving)
    short address                                  → unchanged
    """
    v = (address or "").strip()
    if not v:
        return "???"
    if len(v) <= 12:
        return v
    return f"{v[:5]}…{v[-4:]}"


# ── Core naming functions ───────────────────────────────────────────────

def cex_name(exchange_code: str, account_ref: str | None = None) -> str:
    """CEX display name: "{Exchange} {account_ref}".

    >>> cex_name("bybit", "main")
    'Bybit main'
    >>> cex_name("binance")
    'Binance'
    >>> cex_name("gateio", "bot-1")
    'Gate.io bot-1'
    """
    display = SUPPORTED_CEX_EXCHANGE_LABELS.get(
        (exchange_code or "").strip().lower(),
        (exchange_code or "").strip().title(),
    )
    ref = (account_ref or "").strip()
    return f"{display} {ref}".strip() if ref else display


def dex_name(wallet_address: str, chain: str | None = None) -> str:
    """DEX display name: "{Network} {short_addr}".

    >>> dex_name("0xf909…b34cd8", "ethereum")
    'Ethereum 0xf90…4cd8'
    >>> dex_name("TAAJTev…HVH", "tron")
    'Tron TAAJT…HVH'
    >>> dex_name("7mRZVL…sfd", "solana")
    'Solana 7mRZV…sfd'
    """
    addr = short_addr(wallet_address)
    net = _NETWORK_NAMES.get((chain or "").strip().lower(), "")
    return f"{net} {addr}".strip() if net else addr


def integration_label(item: dict[str, Any]) -> str:
    """Best display name for an integration dict — single entry point.

    Priority:
      1. User custom name (integration.name not matching auto-patterns)
      2. Auto-generated formula (CEX or DEX)
    """
    name = (item.get("name") or "").strip()
    kind = (item.get("kind") or "").strip().lower()

    if name and not _is_auto_name(name, item):
        return name

    if kind == "cex":
        return cex_name(
            (item.get("exchange_code") or ""),
            (item.get("account_ref") or ""),
        )

    wallet = (item.get("wallet_address") or "").strip()
    chain = (item.get("chain") or "").strip()
    if wallet:
        return dex_name(wallet, chain or _chain_from_provider(item))

    return name or (item.get("exchange_code") or item.get("provider") or "source")


# ── Button label (with balance) ─────────────────────────────────────────

def button_label(name: str, total_usd: float | None = None, stale: bool = False) -> str:
    """Build a button label: "{name} · ${value}{⚠️ if stale}".

    >>> button_label("Bybit main", 292.45)
    'Bybit main · $292.45'
    >>> button_label("Ethereum 0xf90…4cd8", 2235, stale=True)
    'Ethereum 0xf90…4cd8 · $2,235 ⚠️'
    """
    parts = [name]
    if total_usd is not None:
        parts.append(f"${_fmt_usd(total_usd)}")
    label = " · ".join(parts)
    if stale:
        label += " ⚠️"
    return label


# ── Service name map builder ────────────────────────────────────────────

def build_service_name_map(integrations: list[dict[str, Any]]) -> dict[str, str]:
    """Map every balance-API service key to a display label.

    Covers canonical keys, legacy keys (okx_wallet_, tron_ton_),
    and #id-qualified keys so lookups always succeed regardless of format.
    """
    result: dict[str, str] = {}
    for item in integrations or []:
        if not isinstance(item, dict):
            continue
        svc = _service_key_from_item(item)
        if not svc:
            continue
        label = integration_label(item)
        int_id = item.get("id")

        result[svc] = label
        if int_id is not None:
            result[f"{svc}#{int_id}"] = label

        # Also register legacy key for tron_ton provider so Balance DB
        # rows with old tron_ton_{addr} format are still found.
        provider = (item.get("provider") or "").strip().lower()
        if provider == "tron_ton":
            addr = (item.get("wallet_address") or "").strip().lower()
            if addr:
                legacy = f"tron_ton_{addr}"
                result[legacy] = label
                if int_id is not None:
                    result[f"{legacy}#{int_id}"] = label

    return result


# ── Default name for creating a new integration ────────────────────────

def default_creation_name(
    *,
    kind: str,
    exchange_code: str = "",
    account_ref: str = "",
    wallet_address: str = "",
    chain: str = "",
) -> str:
    if kind == "cex":
        return cex_name(exchange_code, account_ref)
    return dex_name(wallet_address, chain)


# ── Fallback label for raw service keys (no integration list) ─────────

def service_label(service_key: str) -> str:
    """Best-effort label from a raw balance-API service key alone."""
    base = service_key.split("#", 1)[0]
    addr = _addr_from_base(base)
    if addr:
        return short_addr(addr)
    return SUPPORTED_CEX_EXCHANGE_LABELS.get(base.lower(), base.title())


# ── Internal helpers ────────────────────────────────────────────────────

def _fmt_usd(v: float) -> str:
    """Format USD value: whole-dollar precision for >=1, 2 decimals otherwise."""
    if abs(v) >= 1:
        return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"
    return f"{v:.2f}"


def _is_auto_name(name: str, item: dict[str, Any]) -> bool:
    """True if name looks like an auto-generated default, not user-typed.

    For DEX we ALWAYS treat the stored name as auto-generated because
    the canonical label (with network prefix) is computed from wallet_address + chain.
    Only CEX names can be user-customized.
    """
    kind = (item.get("kind") or "").strip().lower()

    # DEX: always use the generated label, never trust stored name
    if kind == "dex":
        return True

    # CEX: names starting with a raw prefix are internal
    n = name.lower()
    if any(n.startswith(p) for p in _WALLET_PREFIXES):
        return True

    return False


def _chain_from_provider(item: dict[str, Any]) -> str:
    """Infer chain from provider field when chain is missing."""
    p = (item.get("provider") or "").strip().lower()
    if p == "sui":
        return "sui"
    if p == "tron_ton":
        addr = (item.get("wallet_address") or "").strip()
        if addr.startswith(("UQ", "EQ", "uq", "eq")):
            return "ton"
        return "tron"
    if p in ("okx_wallet", "debank"):
        addr = (item.get("wallet_address") or "").strip()
        if addr.startswith(("So", "7m")):
            return "solana"
        return "ethereum"
    return ""


def _addr_from_base(base: str) -> str:
    for prefix in _WALLET_PREFIXES:
        if base.startswith(prefix):
            return base.removeprefix(prefix)
    return ""


def _service_key_from_item(item: dict[str, Any]) -> str:
    kind = (item.get("kind") or "").strip().lower()
    if kind == "cex":
        return (item.get("exchange_code") or "").strip().lower()
    wallet = (item.get("wallet_address") or "").strip().lower()
    if not wallet:
        return ""
    provider = (item.get("provider") or "okx_wallet").strip().lower()
    chain = (item.get("chain") or "").strip().lower()
    if provider == "sui":
        return f"sui_{wallet}"
    if provider == "tron_ton":
        return f"tron_{wallet}" if chain == "tron" else f"ton_{wallet}"
    if chain == "solana":
        return f"sol_{wallet}"
    return f"evm_{wallet}"