"""Inline keyboards for bot views."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu(
    allow_dex: bool = True,
    plan_label: str | None = None,
    can_refresh: bool = True,
    retry_after_seconds: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Spot", callback_data="spot"),
        InlineKeyboardButton(text="Futures", callback_data="futures"),
    )

    if allow_dex:
        builder.row(
            InlineKeyboardButton(text="DEX Wallet", callback_data="dex"),
            InlineKeyboardButton(text="Transactions", callback_data="transactions"),
        )
    else:
        builder.row(InlineKeyboardButton(text="Transactions", callback_data="transactions"))

    builder.row(InlineKeyboardButton(text="Integrations", callback_data="integrations"))

    refresh_text = "Refresh" if can_refresh else f"Refresh ({retry_after_seconds}s)"
    refresh_callback = "refresh" if can_refresh else "noop"

    builder.row(
        InlineKeyboardButton(text="Settings", callback_data="settings"),
        InlineKeyboardButton(text=refresh_text, callback_data=refresh_callback),
    )

    if plan_label:
        builder.row(InlineKeyboardButton(text=f"Plan: {plan_label}", callback_data="noop"))

    return builder.as_markup()


def _exchange_list(prefix: str, exchanges: list[str], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * per_page
    end = start + per_page

    for exchange in exchanges[start:end]:
        builder.button(text=exchange, callback_data=f"{prefix}_{exchange}")
    builder.adjust(2)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"{prefix}_page_{page - 1}"))
    if end < len(exchanges):
        nav.append(InlineKeyboardButton(text=">", callback_data=f"{prefix}_page_{page + 1}"))
    if nav:
        builder.row(*nav)

    builder.row(InlineKeyboardButton(text="Back", callback_data="back_main"))
    return builder.as_markup()


def spot_exchanges(exchanges: list[str], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    return _exchange_list("spot", exchanges, page, per_page)


def futures_exchanges(exchanges: list[str], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    return _exchange_list("futures", exchanges, page, per_page)


def exchange_detail(back_to: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Back", callback_data=f"back_{back_to}"))
    return builder.as_markup()


def dex(page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"dex_page_{page - 1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text=">", callback_data=f"dex_page_{page + 1}"))

    if nav:
        builder.row(*nav)

    builder.row(InlineKeyboardButton(text="Back", callback_data="back_main"))
    return builder.as_markup()


def settings(hide_small: bool) -> InlineKeyboardMarkup:
    status = "ON" if hide_small else "OFF"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"Hide small balances: {status}",
            callback_data="toggle_hide_small",
        )
    )
    builder.row(InlineKeyboardButton(text="Back", callback_data="back_main"))
    return builder.as_markup()


def transactions_filters(
    selected_type: str,
    selected_status: str,
    selected_since_hours: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    type_label = {
        "all": "Type: all",
        "deposit": "Type: deposit",
        "withdrawal": "Type: withdrawal",
    }.get(selected_type, "Type: all")
    status_label = {
        "all": "Status: all",
        "pending": "Status: pending",
        "ok": "Status: ok",
        "failed": "Status: failed",
    }.get(selected_status, "Status: all")
    since_label = f"Window: {selected_since_hours}h"

    builder.row(
        InlineKeyboardButton(text=type_label, callback_data="transactions_filter_type"),
        InlineKeyboardButton(text=status_label, callback_data="transactions_filter_status"),
    )
    builder.row(
        InlineKeyboardButton(text=since_label, callback_data="transactions_filter_since"),
        InlineKeyboardButton(text="Reset filters", callback_data="transactions_filter_reset"),
    )
    return builder.as_markup()


def transactions(
    page: int,
    has_prev: bool,
    has_next: bool,
    selected_type: str,
    selected_status: str,
    selected_since_hours: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="<", callback_data=f"transactions_page_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}", callback_data="noop"))
    if has_next:
        nav.append(InlineKeyboardButton(text=">", callback_data=f"transactions_page_{page + 1}"))
    builder.row(*nav)

    type_label = {
        "all": "Type: all",
        "deposit": "Type: deposit",
        "withdrawal": "Type: withdrawal",
    }.get(selected_type, "Type: all")
    status_label = {
        "all": "Status: all",
        "pending": "Status: pending",
        "ok": "Status: ok",
        "failed": "Status: failed",
    }.get(selected_status, "Status: all")
    since_label = f"Window: {selected_since_hours}h"

    builder.row(
        InlineKeyboardButton(text=type_label, callback_data="transactions_filter_type"),
        InlineKeyboardButton(text=status_label, callback_data="transactions_filter_status"),
    )
    builder.row(
        InlineKeyboardButton(text=since_label, callback_data="transactions_filter_since"),
        InlineKeyboardButton(text="Reset", callback_data="transactions_filter_reset"),
    )
    builder.row(InlineKeyboardButton(text="Refresh transactions", callback_data="transactions_refresh"))
    builder.row(InlineKeyboardButton(text="Back", callback_data="back_main"))
    return builder.as_markup()


def integrations(items: list[dict[str, object]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        integration_id = int(item["id"])
        name = str(item.get("name") or f"integration-{integration_id}")
        is_active = bool(item.get("is_active"))
        status = "ON" if is_active else "OFF"
        builder.row(
            InlineKeyboardButton(
                text=f"{name} [{status}]",
                callback_data=f"integration_select_{integration_id}",
            )
        )

    builder.row(InlineKeyboardButton(text="Refresh list", callback_data="integrations"))
    builder.row(InlineKeyboardButton(text="Back", callback_data="back_main"))
    return builder.as_markup()


def integration_actions(integration_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Disable" if is_active else "Enable",
            callback_data=(
                f"integration_deactivate_{integration_id}"
                if is_active
                else f"integration_activate_{integration_id}"
            ),
        ),
        InlineKeyboardButton(
            text="Refresh",
            callback_data=f"integration_refresh_{integration_id}",
        ),
    )
    builder.row(InlineKeyboardButton(text="Back", callback_data="integrations"))
    return builder.as_markup()
