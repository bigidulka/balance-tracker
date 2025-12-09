from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Dict, Any


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Spot", callback_data="spot"),
                InlineKeyboardButton(text="Futures", callback_data="futures"),
            ],
            [
                InlineKeyboardButton(text="DEX Wallet", callback_data="dex"),
                InlineKeyboardButton(text="Settings", callback_data="settings"),
            ],
            [
                InlineKeyboardButton(text="Refresh", callback_data="refresh"),
            ],
        ]
    )


def back_kb(to: str = "main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Back", callback_data=f"back_{to}")]
        ]
    )


def spot_exchanges_kb(
    exchanges: List[str], page: int = 0, per_page: int = 8
) -> InlineKeyboardMarkup:
    start = page * per_page
    end = start + per_page
    page_exchanges = exchanges[start:end]

    buttons = []
    row = []
    for i, ex in enumerate(page_exchanges):
        row.append(InlineKeyboardButton(text=ex, callback_data=f"spot_{ex}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(text="<", callback_data=f"spot_page_{page-1}")
        )
    if end < len(exchanges):
        nav_row.append(
            InlineKeyboardButton(text=">", callback_data=f"spot_page_{page+1}")
        )
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="Back", callback_data="back_main")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def futures_exchanges_kb(
    exchanges: List[str], page: int = 0, per_page: int = 8
) -> InlineKeyboardMarkup:
    start = page * per_page
    end = start + per_page
    page_exchanges = exchanges[start:end]

    buttons = []
    row = []
    for i, ex in enumerate(page_exchanges):
        row.append(InlineKeyboardButton(text=ex, callback_data=f"futures_{ex}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(text="<", callback_data=f"futures_page_{page-1}")
        )
    if end < len(exchanges):
        nav_row.append(
            InlineKeyboardButton(text=">", callback_data=f"futures_page_{page+1}")
        )
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="Back", callback_data="back_main")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def exchange_detail_kb(back_to: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Back", callback_data=f"back_{back_to}")]
        ]
    )


def assets_pagination_kb(
    back_to: str, current_page: int, total_pages: int, prefix: str
) -> InlineKeyboardMarkup:
    buttons = []

    nav_row = []
    if current_page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="<", callback_data=f"{prefix}_page_{current_page-1}"
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            text=f"{current_page+1}/{total_pages}", callback_data="noop"
        )
    )
    if current_page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text=">", callback_data=f"{prefix}_page_{current_page+1}"
            )
        )

    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="Back", callback_data=f"back_{back_to}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def settings_kb(hide_small: bool) -> InlineKeyboardMarkup:
    status = "ON" if hide_small else "OFF"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Hide small balances: {status}",
                    callback_data="toggle_hide_small",
                )
            ],
            [InlineKeyboardButton(text="Back", callback_data="back_main")],
        ]
    )


def dex_kb(page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    buttons = []

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(text="<", callback_data=f"dex_page_{page-1}")
        )
    if total_pages > 1:
        nav_row.append(
            InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop")
        )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(text=">", callback_data=f"dex_page_{page+1}")
        )

    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="Back", callback_data="back_main")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
