"""
Inline клавиатуры бота
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import messages as msg


# ============================================
# ГЛАВНОЕ МЕНЮ
# ============================================


def main_menu() -> InlineKeyboardMarkup:
    """Главное меню"""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="🔹 Действие 1", callback_data="action_1"),
        InlineKeyboardButton(text="🔹 Действие 2", callback_data="action_2"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
    )

    return builder.as_markup()


# ============================================
# КНОПКА НАЗАД
# ============================================


def back_button(callback_data: str = "menu") -> InlineKeyboardMarkup:
    """Кнопка назад"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=msg.BTN_BACK, callback_data=callback_data),
    )
    return builder.as_markup()


# ============================================
# ПОДТВЕРЖДЕНИЕ
# ============================================


def confirm(confirm_data: str, cancel_data: str = "menu") -> InlineKeyboardMarkup:
    """Кнопки подтверждения"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=msg.BTN_CONFIRM, callback_data=confirm_data),
        InlineKeyboardButton(text=msg.BTN_CANCEL, callback_data=cancel_data),
    )
    return builder.as_markup()


# ============================================
# ПАГИНАЦИЯ (ШАБЛОН)
# ============================================


def paginated_list(
    items: list,
    page: int = 0,
    per_page: int = 5,
    item_callback: str = "item_",
    back_callback: str = "menu",
    page_callback: str = "page_",
) -> InlineKeyboardMarkup:
    """
    Список с пагинацией

    Args:
        items: Список элементов (должны иметь id и name)
        page: Текущая страница
        per_page: Элементов на странице
        item_callback: Префикс callback для элемента
        back_callback: Callback для кнопки назад
        page_callback: Префикс callback для пагинации
    """
    builder = InlineKeyboardBuilder()

    # Элементы текущей страницы
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    for item in page_items:
        builder.row(
            InlineKeyboardButton(
                text=item.name,
                callback_data=f"{item_callback}{item.id}",
            )
        )

    # Пагинация
    total_pages = (len(items) + per_page - 1) // per_page
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="◀️", callback_data=f"{page_callback}{page - 1}"
                )
            )
        nav_buttons.append(
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="▶️", callback_data=f"{page_callback}{page + 1}"
                )
            )
        builder.row(*nav_buttons)

    # Кнопка назад
    builder.row(
        InlineKeyboardButton(text=msg.BTN_BACK, callback_data=back_callback),
    )

    return builder.as_markup()
