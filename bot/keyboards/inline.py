"""Inline keyboards for bot views with structured callback contract."""

from __future__ import annotations

from urllib.parse import urlencode

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.contracts.callbacks import (
    ACTION_BACK,
    ACTION_INPUT_START,
    ACTION_INTEGRATION_ACTIVATE,
    ACTION_INTEGRATION_DEACTIVATE,
    ACTION_INTEGRATION_DELETE,
    ACTION_INTEGRATION_REFRESH,
    ACTION_NOOP,
    ACTION_OPEN,
    ACTION_PAGE,
    ACTION_REFRESH,
    ACTION_RESET,
    ACTION_SELECT,
    ACTION_TOGGLE,
    ROUTE_DEX,
    ROUTE_FUTURES_DETAIL,
    ROUTE_FUTURES_LIST,
    ROUTE_INPUT,
    ROUTE_INTEGRATION_DETAIL,
    ROUTE_INTEGRATION_EXCHANGE_PICKER,
    ROUTE_INTEGRATION_WALLET_PICKER,
    ROUTE_INTEGRATIONS,
    ROUTE_MAIN,
    ROUTE_NOTIFICATIONS,
    ROUTE_PAYMENTS,
    ROUTE_PLAN,
    ROUTE_ADMIN,
    ROUTE_ADMIN_USERS,
    ROUTE_ADMIN_USER_DETAIL,
    ROUTE_SETTINGS,
    ROUTE_SPOT_DETAIL,
    ROUTE_SPOT_LIST,
    ROUTE_TRANSACTIONS,
    encode_input_kind,
    pack_callback,
)
from bot.contracts.exchange_emojis import resolve_exchange_emoji_id
from bot.contracts.exchanges import SUPPORTED_CEX_EXCHANGES
from bot.contracts.wallets import SUPPORTED_WALLET_PROVIDERS
from bot.i18n import locale_label, t
from bot.messages import format_usd
from bot.ui_emoji import UI_ICONS as _ICON_IDS



def _payload(**kwargs: object) -> str:
    return urlencode({k: str(v) for k, v in kwargs.items()})


def _btn(
    text: str,
    callback_data: str,
    icon_key: str,
    *,
    custom_emoji_id: str | None = None,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        icon_custom_emoji_id=custom_emoji_id
        or _ICON_IDS.get(icon_key, _ICON_IDS["noop"]),
    )


def main_menu(
    *,
    rev: int,
    locale: str,
    allow_dex: bool = True,
    plan_label: str | None = None,
    can_refresh: bool = True,
    retry_after_seconds: int = 0,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(
            t(locale, "spot"),
            pack_callback(
                ROUTE_SPOT_LIST,
                ACTION_OPEN,
                rev=rev,
                source=ROUTE_MAIN,
                payload=_payload(page=0),
            ),
            "spot",
        ),
        _btn(
            t(locale, "futures"),
            pack_callback(
                ROUTE_FUTURES_LIST,
                ACTION_OPEN,
                rev=rev,
                source=ROUTE_MAIN,
                payload=_payload(page=0),
            ),
            "futures",
        ),
    )
    if allow_dex:
        builder.row(
            _btn(
                t(locale, "dex_wallet"),
                pack_callback(
                    ROUTE_DEX,
                    ACTION_OPEN,
                    rev=rev,
                    source=ROUTE_MAIN,
                    payload=_payload(page=0),
                ),
                "dex",
            ),
            _btn(
                t(locale, "transactions"),
                pack_callback(
                    ROUTE_TRANSACTIONS,
                    ACTION_OPEN,
                    rev=rev,
                    source=ROUTE_MAIN,
                    payload=_payload(page=0),
                ),
                "transactions",
            ),
        )
    else:
        builder.row(
            _btn(
                t(locale, "transactions"),
                pack_callback(
                    ROUTE_TRANSACTIONS,
                    ACTION_OPEN,
                    rev=rev,
                    source=ROUTE_MAIN,
                    payload=_payload(page=0),
                ),
                "transactions",
            )
        )
    builder.row(
        _btn(
            t(locale, "integrations"),
            pack_callback(ROUTE_INTEGRATIONS, ACTION_OPEN, rev=rev, source=ROUTE_MAIN),
            "integrations",
        ),
        _btn(
            t(locale, "notifications"),
            pack_callback(ROUTE_NOTIFICATIONS, ACTION_OPEN, rev=rev, source=ROUTE_MAIN),
            "notifications",
        ),
    )
    builder.row(
        _btn(
            t(locale, "plan"),
            pack_callback(ROUTE_PLAN, ACTION_OPEN, rev=rev, source=ROUTE_MAIN),
            "plan",
        ),
        _btn(
            t(locale, "settings"),
            pack_callback(ROUTE_SETTINGS, ACTION_OPEN, rev=rev, source=ROUTE_MAIN),
            "settings",
        ),
    )
    refresh_base = t(locale, "refresh")
    if refresh_time_label:
        refresh_base = f"{refresh_base} {refresh_time_label}"
    refresh_text = (
        refresh_base if can_refresh else f"{refresh_base} ({retry_after_seconds}s)"
    )
    builder.row(
        _btn(
            refresh_text,
            pack_callback(
                ROUTE_MAIN, ACTION_REFRESH if can_refresh else ACTION_NOOP, rev=rev
            ),
            "refresh",
        )
    )
    return builder.as_markup()


def _exchange_list(
    *,
    route: str,
    detail_route: str,
    exchanges: list[str],
    labels: list[str] | None = None,
    page: int,
    per_page: int,
    rev: int,
    locale: str,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    """Build exchange list keyboard.

    exchanges — service key list (used for navigation index)
    labels    — human-readable button labels (parallel to exchanges).
                Falls back to exchanges when not provided.
    """
    effective_labels = labels if labels is not None else exchanges
    builder = InlineKeyboardBuilder()
    start = page * per_page
    end = start + per_page
    for idx, (exchange, label) in enumerate(
        zip(exchanges[start:end], effective_labels[start:end]), start=start
    ):
        exchange_emoji_id = resolve_exchange_emoji_id(exchange)
        builder.row(
            _btn(
                label,
                pack_callback(
                    detail_route,
                    ACTION_SELECT,
                    rev=rev,
                    source=route,
                    payload=_payload(i=idx),
                ),
                "wallet",
                custom_emoji_id=exchange_emoji_id,
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            _btn(
                t(locale, "previous"),
                pack_callback(
                    route, ACTION_PAGE, rev=rev, payload=_payload(page=page - 1)
                ),
                "left",
            )
        )
    if end < len(exchanges):
        nav.append(
            _btn(
                t(locale, "next"),
                pack_callback(
                    route, ACTION_PAGE, rev=rev, payload=_payload(page=page + 1)
                ),
                "right",
            )
        )
    if nav:
        builder.row(*nav)
    refresh_text = (
        t(locale, "refresh")
        if not refresh_time_label
        else f"{t(locale, 'refresh')} {refresh_time_label}"
    )
    builder.row(
        _btn(refresh_text, pack_callback(route, ACTION_REFRESH, rev=rev), "refresh")
    )
    builder.row(
        _btn(t(locale, "back"), pack_callback(route, ACTION_BACK, rev=rev), "back")
    )
    return builder.as_markup()


def spot_exchanges(
    exchanges: list[str],
    page: int,
    per_page: int,
    rev: int,
    *,
    locale: str,
    labels: list[str] | None = None,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    return _exchange_list(
        route=ROUTE_SPOT_LIST,
        detail_route=ROUTE_SPOT_DETAIL,
        exchanges=exchanges,
        labels=labels,
        page=page,
        per_page=per_page,
        rev=rev,
        locale=locale,
        refresh_time_label=refresh_time_label,
    )


def futures_exchanges(
    exchanges: list[str],
    page: int,
    per_page: int,
    rev: int,
    *,
    locale: str,
    labels: list[str] | None = None,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    return _exchange_list(
        route=ROUTE_FUTURES_LIST,
        detail_route=ROUTE_FUTURES_DETAIL,
        exchanges=exchanges,
        labels=labels,
        page=page,
        per_page=per_page,
        rev=rev,
        locale=locale,
        refresh_time_label=refresh_time_label,
    )


def exchange_detail(
    route: str, rev: int, *, locale: str, refresh_time_label: str | None = None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    refresh_text = (
        t(locale, "refresh")
        if not refresh_time_label
        else f"{t(locale, 'refresh')} {refresh_time_label}"
    )
    builder.row(
        _btn(refresh_text, pack_callback(route, ACTION_REFRESH, rev=rev), "refresh")
    )
    builder.row(
        _btn(t(locale, "back"), pack_callback(route, ACTION_BACK, rev=rev), "back")
    )
    return builder.as_markup()


def dex(
    page: int,
    total_pages: int,
    rev: int,
    *,
    locale: str,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            _btn(
                t(locale, "previous"),
                pack_callback(
                    ROUTE_DEX, ACTION_PAGE, rev=rev, payload=_payload(page=page - 1)
                ),
                "left",
            )
        )
    if total_pages > 1:
        nav.append(
            _btn(
                f"{page + 1}/{total_pages}",
                pack_callback(ROUTE_DEX, ACTION_NOOP, rev=rev),
                "wallet",
            )
        )
    if page < total_pages - 1:
        nav.append(
            _btn(
                t(locale, "next"),
                pack_callback(
                    ROUTE_DEX, ACTION_PAGE, rev=rev, payload=_payload(page=page + 1)
                ),
                "right",
            )
        )
    if nav:
        builder.row(*nav)
    refresh_text = (
        t(locale, "refresh")
        if not refresh_time_label
        else f"{t(locale, 'refresh')} {refresh_time_label}"
    )
    builder.row(
        _btn(refresh_text, pack_callback(ROUTE_DEX, ACTION_REFRESH, rev=rev), "refresh")
    )
    builder.row(
        _btn(t(locale, "back"), pack_callback(ROUTE_DEX, ACTION_BACK, rev=rev), "back")
    )
    return builder.as_markup()


def dex_wallets(
    items: list[str],
    page: int,
    per_page: int,
    rev: int,
    *,
    locale: str,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * per_page
    end = start + per_page
    for idx, label in enumerate(items[start:end], start=start):
        builder.row(
            _btn(
                label,
                pack_callback(
                    ROUTE_DEX,
                    ACTION_SELECT,
                    rev=rev,
                    source=ROUTE_DEX,
                    payload=_payload(i=idx, page=0),
                ),
                "wallet",
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            _btn(
                t(locale, "previous"),
                pack_callback(
                    ROUTE_DEX, ACTION_PAGE, rev=rev, payload=_payload(page=page - 1)
                ),
                "left",
            )
        )
    if end < len(items):
        nav.append(
            _btn(
                t(locale, "next"),
                pack_callback(
                    ROUTE_DEX, ACTION_PAGE, rev=rev, payload=_payload(page=page + 1)
                ),
                "right",
            )
        )
    if nav:
        builder.row(*nav)
    refresh_text = (
        t(locale, "refresh")
        if not refresh_time_label
        else f"{t(locale, 'refresh')} {refresh_time_label}"
    )
    builder.row(
        _btn(refresh_text, pack_callback(ROUTE_DEX, ACTION_REFRESH, rev=rev), "refresh")
    )
    builder.row(
        _btn(t(locale, "back"), pack_callback(ROUTE_DEX, ACTION_BACK, rev=rev), "back")
    )
    return builder.as_markup()


def dex_wallet_detail(
    wallet_index: int,
    page: int,
    total_pages: int,
    rev: int,
    *,
    locale: str,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            _btn(
                t(locale, "previous"),
                pack_callback(
                    ROUTE_DEX,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(i=wallet_index, page=page - 1),
                ),
                "left",
            )
        )
    if total_pages > 1:
        nav.append(
            _btn(
                f"{page + 1}/{total_pages}",
                pack_callback(ROUTE_DEX, ACTION_NOOP, rev=rev),
                "wallet",
            )
        )
    if page < total_pages - 1:
        nav.append(
            _btn(
                t(locale, "next"),
                pack_callback(
                    ROUTE_DEX,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(i=wallet_index, page=page + 1),
                ),
                "right",
            )
        )
    if nav:
        builder.row(*nav)
    refresh_text = (
        t(locale, "refresh")
        if not refresh_time_label
        else f"{t(locale, 'refresh')} {refresh_time_label}"
    )
    builder.row(
        _btn(
            refresh_text,
            pack_callback(
                ROUTE_DEX,
                ACTION_REFRESH,
                rev=rev,
                payload=_payload(i=wallet_index, page=page),
            ),
            "refresh",
        )
    )
    builder.row(
        _btn(t(locale, "back"), pack_callback(ROUTE_DEX, ACTION_BACK, rev=rev), "back")
    )
    return builder.as_markup()


def settings(
    hide_small: bool,
    language: str,
    rev: int,
    *,
    locale: str,
    is_admin: bool = False,
    display_currency: str = "USD",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(
            f"{t(locale, 'language')}: {locale_label(language)}",
            pack_callback(
                ROUTE_SETTINGS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="language"),
            ),
            "settings",
        )
    )
    builder.row(
        _btn(
            f"{t(locale, 'display_currency')}: {display_currency}",
            pack_callback(
                ROUTE_SETTINGS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="currency"),
            ),
            "settings",
        )
    )
    builder.row(
        _btn(
            f"{t(locale, 'hide_small_balances')}: {t(locale, 'on') if hide_small else t(locale, 'off')}",
            pack_callback(ROUTE_SETTINGS, ACTION_TOGGLE, rev=rev),
            "toggle",
        )
    )
    if is_admin:
        builder.row(
            _btn(
                t(locale, "admin_panel"),
                pack_callback(ROUTE_ADMIN, ACTION_OPEN, rev=rev, source=ROUTE_SETTINGS),
                "admin",
            )
        )
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_SETTINGS, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()



def plan_screen(
    rev: int,
    *,
    locale: str,
    plans: list[dict[str, object]],
    current_plan_code: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        plan_code = str(plan.get("code") or "").strip().lower()
        if not plan_code:
            continue
        plan_name = str(plan.get("name") or plan_code.upper())
        if plan_code == current_plan_code:
            label = f"{plan_name} · {t(locale, 'current_plan_short')}"
            callback = pack_callback(ROUTE_PLAN, ACTION_NOOP, rev=rev)
        else:
            item_price = float(plan.get("price_monthly") or 0.0)
            price_label = format_usd(item_price) if item_price > 0 else t(locale, "free")
            label = f"{plan_name} · {price_label}"
            callback = pack_callback(
                ROUTE_PLAN,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(plan_code=plan_code),
            )

        builder.row(_btn(label, callback, "plan"))
    builder.row(
        _btn(
            t(locale, "top_up_balance"),
            pack_callback(ROUTE_PAYMENTS, ACTION_OPEN, rev=rev, source=ROUTE_PLAN),
            "wallet",
        ),
        _btn(
            t(locale, "apply_promo"),
            pack_callback(
                ROUTE_INPUT,
                ACTION_INPUT_START,
                rev=rev,
                source=ROUTE_PLAN,
                payload=_payload(kind=encode_input_kind("promo_code")),
            ),
            "input",
        ),
    )
    builder.row(
        _btn(t(locale, "back"), pack_callback(ROUTE_PLAN, ACTION_BACK, rev=rev), "back")
    )
    return builder.as_markup()


def payments_screen(
    *,
    invoice_id: int | None,
    invoice_url: str | None = None,
    rev: int,
    locale: str,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # User wallet / external wallet entrypoint
    builder.row(
        InlineKeyboardButton(
            text=(
                "🤖 CryptoBot · user wallet"
                if locale == "en"
                else "🤖 CryptoBot · кошелек пользователя"
            ),
            url="https://t.me/CryptoBot",
        )
    )
    # App wallet top-up shortcuts
    for amount in (10, 25, 50, 100):
        builder.row(
            _btn(
                f"💳 +${amount}",
                pack_callback(
                    ROUTE_PAYMENTS,
                    ACTION_SELECT,
                    rev=rev,
                    payload=_payload(amount=amount),
                ),
                "wallet",
            )
        )
    if invoice_url:
        builder.row(
            InlineKeyboardButton(
                text=("🧾 Open invoice" if locale == "en" else "🧾 Открыть чек"),
                url=invoice_url,
            )
        )
    if invoice_id is not None:
        builder.row(
            _btn(
                t(locale, "check_payment"),
                pack_callback(
                    ROUTE_PAYMENTS,
                    ACTION_REFRESH,
                    rev=rev,
                    payload=_payload(id=invoice_id),
                ),
                "refresh",
            )
        )
    refresh_text = (
        t(locale, "refresh")
        if not refresh_time_label
        else f"{t(locale, 'refresh')} {refresh_time_label}"
    )
    builder.row(
        _btn(
            refresh_text, pack_callback(ROUTE_PAYMENTS, ACTION_OPEN, rev=rev), "refresh"
        )
    )
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_PAYMENTS, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()


def admin_screen(rev: int, *, locale: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(
            t(locale, "users"),
            pack_callback(
                ROUTE_ADMIN_USERS,
                ACTION_OPEN,
                rev=rev,
                source=ROUTE_ADMIN,
                payload=_payload(page=0),
            ),
            "admin",
        )
    )
    builder.row(
        _btn(
            t(locale, "back"), pack_callback(ROUTE_ADMIN, ACTION_BACK, rev=rev), "back"
        )
    )
    return builder.as_markup()


def admin_users(
    *,
    items: list[dict[str, object]],
    page: int,
    has_prev: bool,
    has_next: bool,
    rev: int,
    locale: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        user_id = int(item.get("user_id") or 0)
        organization_id = int(item.get("organization_id") or 0)
        username = str(item.get("telegram_username") or "").strip()
        full_name = str(
            item.get("telegram_full_name") or item.get("full_name") or ""
        ).strip()
        label = (
            f"@{username}"
            if username
            else (full_name or str(item.get("email") or f"user-{user_id}"))
        )
        builder.row(
            _btn(
                label,
                pack_callback(
                    ROUTE_ADMIN_USER_DETAIL,
                    ACTION_SELECT,
                    rev=rev,
                    source=ROUTE_ADMIN_USERS,
                    payload=_payload(user_id=user_id, organization_id=organization_id),
                ),
                "admin",
            )
        )
    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(
            _btn(
                t(locale, "previous"),
                pack_callback(
                    ROUTE_ADMIN_USERS,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(page=page - 1),
                ),
                "left",
            )
        )
    if has_next:
        nav.append(
            _btn(
                t(locale, "next"),
                pack_callback(
                    ROUTE_ADMIN_USERS,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(page=page + 1),
                ),
                "right",
            )
        )
    if nav:
        builder.row(*nav)
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_ADMIN_USERS, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()


def admin_user_detail(
    *,
    user_id: int,
    organization_id: int,
    rev: int,
    locale: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(
            t(locale, "refresh"),
            pack_callback(
                ROUTE_ADMIN_USER_DETAIL,
                ACTION_REFRESH,
                rev=rev,
                payload=_payload(user_id=user_id, organization_id=organization_id),
            ),
            "refresh",
        )
    )
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_ADMIN_USER_DETAIL, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()


def transactions(
    *,
    page: int,
    total_pages: int = 0,
    has_prev: bool,
    has_next: bool,
    selected_type: str,
    selected_status: str,
    selected_since_hours: int,
    rev: int,
    locale: str,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(
            _btn(
                t(locale, "previous"),
                pack_callback(
                    ROUTE_TRANSACTIONS,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(page=page - 1),
                ),
                "left",
            )
        )
    if total_pages > 1:
        nav.append(
            _btn(
                f"{page + 1}",
                pack_callback(ROUTE_TRANSACTIONS, ACTION_NOOP, rev=rev),
                "wallet",
            )
        )
    if has_next:
        nav.append(
            _btn(
                t(locale, "next"),
                pack_callback(
                    ROUTE_TRANSACTIONS,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(page=page + 1),
                ),
                "right",
            )
        )
    if nav:
        builder.row(*nav)
    builder.row(
        _btn(
            f"{t(locale, 'type')}: {t(locale, 'all')}",
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="tx_type", value="all"),
            ),
            "filter",
        ),
        _btn(
            f"{t(locale, 'type')}: {t(locale, 'deposit')}",
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="tx_type", value="deposit"),
            ),
            "filter",
        ),
        _btn(
            f"{t(locale, 'type')}: {t(locale, 'withdrawal')}",
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="tx_type", value="withdrawal"),
            ),
            "filter",
        ),
    )
    builder.row(
        _btn(
            f"{t(locale, 'state')}: {t(locale, 'all')}",
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="tx_status", value="all"),
            ),
            "filter",
        ),
        _btn(
            f"{t(locale, 'state')}: {t(locale, 'ok')}",
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="tx_status", value="ok"),
            ),
            "filter",
        ),
    )
    builder.row(
        _btn(
            f"{t(locale, 'state')}: {t(locale, 'pending')}",
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="tx_status", value="pending"),
            ),
            "filter",
        ),
        _btn(
            f"{t(locale, 'state')}: {t(locale, 'failed')}",
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="tx_status", value="failed"),
            ),
            "filter",
        ),
    )
    builder.row(
        _btn(
            f"24h{' •' if selected_since_hours == 24 else ''}",
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="tx_since_hours", value=24),
            ),
            "filter",
        ),
        _btn(
            f"7d{' •' if selected_since_hours == 24 * 7 else ''}",
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="tx_since_hours", value=24 * 7),
            ),
            "filter",
        ),
        _btn(
            f"30d{' •' if selected_since_hours == 24 * 30 else ''}",
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_SELECT,
                rev=rev,
                payload=_payload(field="tx_since_hours", value=24 * 30),
            ),
            "filter",
        ),
    )
    builder.row(
        _btn(
            t(locale, "custom_window"),
            pack_callback(
                ROUTE_INPUT,
                ACTION_INPUT_START,
                rev=rev,
                source=ROUTE_TRANSACTIONS,
                payload=_payload(kind=encode_input_kind("tx_since_hours")),
            ),
            "input",
        ),
        _btn(
            t(locale, "reset"),
            pack_callback(ROUTE_TRANSACTIONS, ACTION_RESET, rev=rev),
            "reset",
        ),
    )
    refresh_text = (
        t(locale, "refresh_transactions")
        if not refresh_time_label
        else f"{t(locale, 'refresh_transactions')} {refresh_time_label}"
    )
    builder.row(
        _btn(
            refresh_text,
            pack_callback(ROUTE_TRANSACTIONS, ACTION_REFRESH, rev=rev),
            "refresh",
        )
    )
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_TRANSACTIONS, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()


def transaction_sources(
    items: list[str],
    page: int,
    per_page: int,
    rev: int,
    *,
    locale: str,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * per_page
    end = start + per_page
    for idx, label in enumerate(items[start:end], start=start):
        builder.row(
            _btn(
                label,
                pack_callback(
                    ROUTE_TRANSACTIONS,
                    ACTION_SELECT,
                    rev=rev,
                    source=ROUTE_TRANSACTIONS,
                    payload=_payload(i=idx, page=0),
                ),
                "transactions",
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            _btn(
                t(locale, "previous"),
                pack_callback(
                    ROUTE_TRANSACTIONS,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(page=page - 1),
                ),
                "left",
            )
        )
    if end < len(items):
        nav.append(
            _btn(
                t(locale, "next"),
                pack_callback(
                    ROUTE_TRANSACTIONS,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(page=page + 1),
                ),
                "right",
            )
        )
    if nav:
        builder.row(*nav)
    refresh_text = (
        t(locale, "refresh_transactions")
        if not refresh_time_label
        else f"{t(locale, 'refresh_transactions')} {refresh_time_label}"
    )
    builder.row(
        _btn(
            refresh_text,
            pack_callback(ROUTE_TRANSACTIONS, ACTION_REFRESH, rev=rev),
            "refresh",
        )
    )
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_TRANSACTIONS, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()


def transaction_detail(
    source_index: int,
    page: int,
    has_prev: bool,
    has_next: bool,
    rev: int,
    *,
    total_pages: int = 0,
    locale: str,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(
            _btn(
                t(locale, "previous"),
                pack_callback(
                    ROUTE_TRANSACTIONS,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(i=source_index, page=page - 1),
                ),
                "left",
            )
        )
    if total_pages > 1:
        nav.append(
            _btn(
                f"{page + 1}",
                pack_callback(ROUTE_TRANSACTIONS, ACTION_NOOP, rev=rev),
                "transactions",
            )
        )
    if has_next:
        nav.append(
            _btn(
                t(locale, "next"),
                pack_callback(
                    ROUTE_TRANSACTIONS,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(i=source_index, page=page + 1),
                ),
                "right",
            )
        )
    if nav:
        builder.row(*nav)
    refresh_text = (
        t(locale, "refresh_transactions")
        if not refresh_time_label
        else f"{t(locale, 'refresh_transactions')} {refresh_time_label}"
    )
    builder.row(
        _btn(
            refresh_text,
            pack_callback(
                ROUTE_TRANSACTIONS,
                ACTION_REFRESH,
                rev=rev,
                payload=_payload(i=source_index, page=page),
            ),
            "refresh",
        )
    )
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_TRANSACTIONS, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()


def _integration_button_status(item: dict[str, object], *, locale: str) -> str:
    if not bool(item.get("is_active")):
        return t(locale, "off")
    health = str(item.get("health_status") or "").strip().lower()
    if health == "problem":
        return t(locale, "integration_problem")
    if health == "warning":
        return t(locale, "integration_warning")
    if health == "ok":
        return t(locale, "integration_ok")
    if health == "unknown":
        return t(locale, "unknown")
    return t(locale, "on")


def integrations(
    items: list[dict[str, object]],
    rev: int,
    *,
    page: int = 0,
    per_page: int = 8,
    locale: str,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]
    for item in page_items:
        integration_id = int(item["id"])
        name = str(
            item.get("display_name")
            or item.get("name")
            or f"integration-{integration_id}"
        )
        status = _integration_button_status(item, locale=locale)
        exchange_emoji_id = resolve_exchange_emoji_id(
            str(item.get("exchange_code") or item.get("provider") or name)
        )
        builder.row(
            _btn(
                f"{name} [{status}]",
                pack_callback(
                    ROUTE_INTEGRATION_DETAIL,
                    ACTION_SELECT,
                    rev=rev,
                    source=ROUTE_INTEGRATIONS,
                    payload=_payload(id=integration_id),
                ),
                "wallet",
                custom_emoji_id=exchange_emoji_id,
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            _btn(
                t(locale, "previous"),
                pack_callback(
                    ROUTE_INTEGRATIONS,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(page=page - 1),
                ),
                "left",
            )
        )
    if end < len(items):
        nav.append(
            _btn(
                t(locale, "next"),
                pack_callback(
                    ROUTE_INTEGRATIONS,
                    ACTION_PAGE,
                    rev=rev,
                    payload=_payload(page=page + 1),
                ),
                "right",
            )
        )
    if nav:
        builder.row(*nav)
    builder.row(
        _btn(
            t(locale, "add_exchange"),
            pack_callback(
                ROUTE_INTEGRATION_EXCHANGE_PICKER,
                ACTION_OPEN,
                rev=rev,
                source=ROUTE_INTEGRATIONS,
            ),
            "add",
        ),
        _btn(
            t(locale, "add_wallet"),
            pack_callback(
                ROUTE_INTEGRATION_WALLET_PICKER,
                ACTION_OPEN,
                rev=rev,
                source=ROUTE_INTEGRATIONS,
            ),
            "add",
        ),
    )
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_INTEGRATIONS, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()


def integration_actions(
    integration_id: int,
    is_active: bool,
    rev: int,
    *,
    locale: str,
    refresh_time_label: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(
            t(locale, "disable") if is_active else t(locale, "enable"),
            pack_callback(
                ROUTE_INTEGRATION_DETAIL,
                ACTION_INTEGRATION_DEACTIVATE
                if is_active
                else ACTION_INTEGRATION_ACTIVATE,
                rev=rev,
                payload=_payload(id=integration_id),
            ),
            "disable" if is_active else "enable",
        ),
    )
    builder.row(
        _btn(
            t(locale, "rename"),
            pack_callback(
                ROUTE_INPUT,
                ACTION_INPUT_START,
                rev=rev,
                source=ROUTE_INTEGRATION_DETAIL,
                payload=_payload(
                    kind=encode_input_kind("integration_rename"), id=integration_id
                ),
            ),
            "input",
        ),
        _btn(
            t(locale, "delete"),
            pack_callback(
                ROUTE_INTEGRATION_DETAIL,
                ACTION_INTEGRATION_DELETE,
                rev=rev,
                payload=_payload(id=integration_id),
            ),
            "delete",
        ),
    )
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_INTEGRATION_DETAIL, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()


def integration_exchange_picker(rev: int, *, locale: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for code, label in SUPPORTED_CEX_EXCHANGES:
        exchange_emoji_id = resolve_exchange_emoji_id(
            code
        ) or resolve_exchange_emoji_id(label)
        row.append(
            _btn(
                label,
                pack_callback(
                    ROUTE_INTEGRATION_EXCHANGE_PICKER,
                    ACTION_SELECT,
                    rev=rev,
                    source=ROUTE_INTEGRATIONS,
                    payload=_payload(exchange_code=code),
                ),
                "wallet",
                custom_emoji_id=exchange_emoji_id,
            )
        )
        if len(row) == 2:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_INTEGRATION_EXCHANGE_PICKER, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()


def integration_wallet_picker(rev: int, *, locale: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code, label in SUPPORTED_WALLET_PROVIDERS:
        builder.row(
            _btn(
                label,
                pack_callback(
                    ROUTE_INTEGRATION_WALLET_PICKER,
                    ACTION_SELECT,
                    rev=rev,
                    source=ROUTE_INTEGRATIONS,
                    payload=_payload(wallet_provider=code),
                ),
                "wallet",
            )
        )
    builder.row(
        _btn(
            t(locale, "back"),
            pack_callback(ROUTE_INTEGRATION_WALLET_PICKER, ACTION_BACK, rev=rev),
            "back",
        )
    )
    return builder.as_markup()


def input_waiting(rev: int, *, locale: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(
            t(locale, "back"), pack_callback(ROUTE_INPUT, ACTION_BACK, rev=rev), "back"
        )
    )
    return builder.as_markup()
