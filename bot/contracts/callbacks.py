"""Structured callback contract for bot navigation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode

_CALLBACK_PREFIX = "ui:"
_CALLBACK_VERSION = "1"

INPUT_KIND_ALIASES = {
    "tx_since_hours": "th",
    "integration_dex_wallet_address": "dw",
    "integration_dex_chain": "dc",
    "integration_cex_account_ref": "ca",
    "integration_cex_api_key": "ck",
    "integration_cex_api_secret": "cs",
    "integration_cex_api_password": "cp",
    "integration_rename": "rn",
    "promo_code": "pc",
}
INPUT_KIND_BY_ALIAS = {alias: kind for kind, alias in INPUT_KIND_ALIASES.items()}


@dataclass(frozen=True)
class CallbackCommand:
    route: str
    action: str
    rev: int = 0
    source: str | None = None
    payload: str | None = None


ROUTE_MAIN = "m"
ROUTE_SPOT_LIST = "sl"
ROUTE_SPOT_DETAIL = "sd"
ROUTE_FUTURES_LIST = "fl"
ROUTE_FUTURES_DETAIL = "fd"
ROUTE_DEX = "dx"
ROUTE_TRANSACTIONS = "tx"
ROUTE_INTEGRATIONS = "ig"
ROUTE_INTEGRATION_DETAIL = "id"
ROUTE_INTEGRATION_EXCHANGE_PICKER = "ix"
ROUTE_INTEGRATION_WALLET_PICKER = "iw"
ROUTE_PLAN = "pl"
ROUTE_PAYMENTS = "py"
ROUTE_ADMIN = "am"
ROUTE_ADMIN_USERS = "au"
ROUTE_ADMIN_USER_DETAIL = "ud"
ROUTE_SETTINGS = "st"
ROUTE_INPUT = "in"


ACTION_OPEN = "o"
ACTION_PAGE = "p"
ACTION_SELECT = "s"
ACTION_BACK = "b"
ACTION_REFRESH = "r"
ACTION_NOOP = "n"
ACTION_TOGGLE = "t"
ACTION_CYCLE = "c"
ACTION_RESET = "z"
ACTION_INPUT_START = "i"
ACTION_INTEGRATION_ACTIVATE = "ia"
ACTION_INTEGRATION_DEACTIVATE = "id"
ACTION_INTEGRATION_REFRESH = "ir"
ACTION_INTEGRATION_DELETE = "ix"


def pack_callback(
    route: str,
    action: str,
    *,
    rev: int,
    source: str | None = None,
    payload: str | None = None,
) -> str:
    query = {
        "v": _CALLBACK_VERSION,
        "r": route,
        "a": action,
        "rev": str(rev),
    }
    if source:
        query["s"] = source
    if payload:
        query["p"] = payload
    return f"{_CALLBACK_PREFIX}{urlencode(query)}"


def parse_callback(data: str | None) -> CallbackCommand | None:
    if not data or not data.startswith(_CALLBACK_PREFIX):
        return None

    raw_query = data.removeprefix(_CALLBACK_PREFIX)
    parsed = parse_qs(raw_query)

    version = _first(parsed, "v")
    if version != _CALLBACK_VERSION:
        return None

    route = _first(parsed, "r")
    action = _first(parsed, "a")
    if not route or not action:
        return None

    rev_raw = _first(parsed, "rev") or "0"
    try:
        rev = int(rev_raw)
    except ValueError:
        rev = 0

    return CallbackCommand(
        route=route,
        action=action,
        rev=rev,
        source=_first(parsed, "s"),
        payload=_first(parsed, "p"),
    )


def _first(values: dict[str, list[str]], key: str) -> str | None:
    row = values.get(key)
    if not row:
        return None
    return row[0]


def parse_payload_int(payload: str | None, default: int = 0) -> int:
    if payload is None:
        return default
    try:
        return int(payload)
    except ValueError:
        return default


def parse_payload_jsonish(payload: str | None) -> dict[str, Any]:
    if not payload:
        return {}
    pairs = parse_qs(payload)
    return {k: v[0] for k, v in pairs.items() if v}


def encode_input_kind(kind: str) -> str:
    return INPUT_KIND_ALIASES.get(kind, kind)


def decode_input_kind(kind: str | None) -> str:
    if not kind:
        return ""
    return INPUT_KIND_BY_ALIAS.get(kind, kind)
