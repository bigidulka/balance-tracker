from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.services.backend_api import BackendApi, BackendError
from app.services.navigation import NavigationService, ScreenMeta
from app.states import WalletFlow
from app.ui.screens import (
    error_screen,
    home_screen,
    input_screen,
    loading_screen,
    portfolio_screen,
    transactions_screen,
    wallet_screen,
    watchlist_screen,
)

router = Router()
START_BANNER = Path(__file__).resolve().parent.parent / "assets" / "start-banner.png"
PHOTO_CAPTION_LIMIT = 1024


def _is_wallet_address(value: str | None) -> bool:
    if not value:
        return False
    candidate = value.strip()
    if candidate.startswith("0x") and len(candidate) == 42:
        return True
    return 32 <= len(candidate) <= 44 and candidate.isalnum() and all(ch not in "0OIl" for ch in candidate)


async def _safe_delete_message(bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        pass


async def _delete_user_message(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


async def _set_root_message(
    state: FSMContext,
    navigation: NavigationService,
    message_id: int,
    kind: str,
) -> None:
    await navigation.set_root_message(state, message_id)
    await state.update_data(main_message_kind=kind)


async def _main_message_id(state: FSMContext, navigation: NavigationService) -> int:
    message_id = await navigation.main_message_id(state)
    if not message_id:
        raise RuntimeError("Main message is not initialized")
    return message_id


async def _main_message_kind(state: FSMContext) -> str:
    data = await state.get_data()
    return str(data.get("main_message_kind", "text"))


async def _bootstrap_home_message(
    message: Message,
    state: FSMContext,
    navigation: NavigationService,
) -> None:
    screen, text, keyboard = home_screen()
    previous_root_id = await navigation.main_message_id(state)
    await _safe_delete_message(message.bot, message.chat.id, previous_root_id)
    await state.clear()

    if START_BANNER.exists():
        sent = await message.answer_photo(
            FSInputFile(START_BANNER),
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        await _set_root_message(state, navigation, sent.message_id, "photo")
    else:
        sent = await message.answer(text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await _set_root_message(state, navigation, sent.message_id, "text")

    await navigation.reset(state, screen)


async def _ensure_root_proxy_message(
    message: Message,
    state: FSMContext,
    navigation: NavigationService,
) -> Message:
    root_message_id = await navigation.main_message_id(state)
    if root_message_id is None:
        await _bootstrap_home_message(message, state, navigation)
        root_message_id = await _main_message_id(state, navigation)

    if message.message_id != root_message_id:
        return message.model_copy(update={"message_id": root_message_id})
    return message


async def _send_text_root(
    message: Message,
    state: FSMContext,
    navigation: NavigationService,
    text: str,
    reply_markup,
) -> None:
    bot = message.bot
    chat_id = message.chat.id
    message_id = await _main_message_id(state, navigation)
    kind = await _main_message_kind(state)

    if kind == "text":
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as error:
            if "message is not modified" in str(error):
                return

    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )
    await _safe_delete_message(bot, chat_id, message_id)
    await _set_root_message(state, navigation, sent.message_id, "text")


async def _send_home_root(
    message: Message,
    state: FSMContext,
    navigation: NavigationService,
    text: str,
    reply_markup,
) -> None:
    bot = message.bot
    chat_id = message.chat.id
    message_id = await _main_message_id(state, navigation)
    kind = await _main_message_kind(state)

    if START_BANNER.exists():
        if kind == "photo" and len(text) <= PHOTO_CAPTION_LIMIT:
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
                return
            except TelegramBadRequest as error:
                if "message is not modified" in str(error):
                    return

        sent = await bot.send_photo(
            chat_id=chat_id,
            photo=FSInputFile(START_BANNER),
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
        await _safe_delete_message(bot, chat_id, message_id)
        await _set_root_message(state, navigation, sent.message_id, "photo")
        return

    await _send_text_root(message, state, navigation, text, reply_markup)


async def _render_home(message: Message, state: FSMContext, navigation: NavigationService) -> None:
    message = await _ensure_root_proxy_message(message, state, navigation)
    screen, text, keyboard = home_screen()
    await navigation.reset(state, screen)
    await _send_home_root(message, state, navigation, text, keyboard)


async def _render_watchlist(
    message: Message,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
    telegram_user_id: int,
) -> None:
    message = await _ensure_root_proxy_message(message, state, navigation)
    items = await backend_api.get_watchlist(telegram_user_id)
    screen, text, keyboard = watchlist_screen(items)
    await navigation.replace(state, screen)
    await _send_text_root(message, state, navigation, text, keyboard)


async def _render_wallet(
    message: Message,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
    telegram_user_id: int,
    address: str,
    push_history: bool,
) -> None:
    message = await _ensure_root_proxy_message(message, state, navigation)
    short_address = f"{address[:6]}...{address[-4:]}" if len(address) > 12 else address
    text, keyboard = loading_screen("Загрузка", f"Получаю сводку по {short_address}")
    await _send_text_root(message, state, navigation, text, keyboard)
    summary = await backend_api.get_summary(telegram_user_id, address)
    screen, text, keyboard = wallet_screen(summary)
    if push_history:
        await navigation.go_to(state, screen)
    else:
        await navigation.replace(state, screen)
    await _send_text_root(message, state, navigation, text, keyboard)


async def _render_portfolio(
    message: Message,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
    telegram_user_id: int,
    address: str,
    page: int,
    push_history: bool,
) -> None:
    message = await _ensure_root_proxy_message(message, state, navigation)
    text, keyboard = loading_screen("Загрузка", "Собираю весь портфель...")
    await _send_text_root(message, state, navigation, text, keyboard)
    portfolio = await backend_api.get_portfolio(telegram_user_id, address, page=page, page_size=8)
    await state.update_data(portfolio_view={"address": address, "page": portfolio.page})
    screen, text, keyboard = portfolio_screen(portfolio)
    if push_history:
        await navigation.go_to(state, screen)
    else:
        await navigation.replace(state, screen)
    await _send_text_root(message, state, navigation, text, keyboard)


async def _render_transactions(
    message: Message,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
    telegram_user_id: int,
    address: str,
    cursor: int,
    prev_cursors: list[int],
    hide_scam: bool,
    page_number: int,
    push_history: bool,
) -> None:
    message = await _ensure_root_proxy_message(message, state, navigation)
    text, keyboard = loading_screen("Загрузка", "Подгружаю транзакции...")
    await _send_text_root(message, state, navigation, text, keyboard)
    page = await backend_api.get_transactions(
        telegram_user_id,
        address,
        cursor=cursor,
        page_size=5,
        hide_scam=hide_scam,
    )
    await state.update_data(
        transactions_view={
            "address": address,
            "cursor": cursor,
            "prev_cursors": prev_cursors,
            "next_cursor": page.nextCursor,
            "hide_scam": hide_scam,
            "page_number": page_number,
        }
    )
    screen, text, keyboard = transactions_screen(
        page,
        page_number=page_number,
        has_prev_page=bool(prev_cursors),
    )
    if push_history:
        await navigation.go_to(state, screen)
    else:
        await navigation.replace(state, screen)
    await _send_text_root(message, state, navigation, text, keyboard)


async def _show_error(
    message: Message,
    state: FSMContext,
    navigation: NavigationService,
    error: Exception | str,
) -> None:
    message = await _ensure_root_proxy_message(message, state, navigation)
    text, keyboard = error_screen(str(error))
    await _send_text_root(message, state, navigation, text, keyboard)


@router.message(CommandStart())
async def handle_start(
    message: Message,
    state: FSMContext,
    navigation: NavigationService,
) -> None:
    if not message.from_user:
        return
    await _delete_user_message(message)
    await _bootstrap_home_message(message, state, navigation)


@router.message()
async def handle_message(
    message: Message,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    if not message.from_user:
        return

    if await navigation.main_message_id(state) is None:
        await _bootstrap_home_message(message, state, navigation)

    await _delete_user_message(message)
    root_message_id = await _main_message_id(state, navigation)
    current_state = await state.get_state()
    data = await state.get_data()
    incoming_text = (message.text or "").strip()
    proxy_message = message.model_copy(update={"message_id": root_message_id})

    if current_state == WalletFlow.waiting_wallet_input:
        action = data.get("awaiting_input")
        if not _is_wallet_address(incoming_text):
            text, keyboard = input_screen(
                str(action or "quick_lookup"),
                "Нужен корректный адрес EVM или Solana.",
            )
            await _send_text_root(proxy_message, state, navigation, text, keyboard)
            return

        await state.set_state(None)
        await state.update_data(awaiting_input=None)
        try:
            if action == "add_watch":
                await backend_api.add_watch_wallet(message.from_user.id, incoming_text)
            await _render_wallet(
                proxy_message,
                state,
                backend_api,
                navigation,
                message.from_user.id,
                incoming_text,
                push_history=False,
            )
        except BackendError as error:
            await _show_error(proxy_message, state, navigation, error)
        return

    if current_state == WalletFlow.waiting_wallet_label:
        address = str(data.get("rename_wallet_address") or "")
        if not address:
            await state.set_state(None)
            await _render_home(proxy_message, state, navigation)
            return

        label = incoming_text.strip()
        if not label or len(label) > 64:
            text, keyboard = input_screen(
                "rename_label",
                "Введите имя от 1 до 64 символов.",
                current_label=data.get("rename_wallet_label"),
            )
            await _send_text_root(proxy_message, state, navigation, text, keyboard)
            return

        await state.set_state(None)
        await state.update_data(awaiting_input=None, rename_wallet_address=None, rename_wallet_label=None)
        try:
            await backend_api.rename_watch_wallet(message.from_user.id, address, label)
            await _render_wallet(
                proxy_message,
                state,
                backend_api,
                navigation,
                message.from_user.id,
                address,
                push_history=False,
            )
        except BackendError as error:
            await _show_error(proxy_message, state, navigation, error)
        return

    if _is_wallet_address(incoming_text):
        try:
            await _render_wallet(
                proxy_message,
                state,
                backend_api,
                navigation,
                message.from_user.id,
                incoming_text,
                push_history=True,
            )
        except BackendError as error:
            await _show_error(proxy_message, state, navigation, error)
        return

    await _render_home(proxy_message, state, navigation)


@router.callback_query(F.data == "nav:home")
async def nav_home(callback: CallbackQuery, state: FSMContext, navigation: NavigationService) -> None:
    await callback.answer()
    if callback.message:
        await _render_home(callback.message, state, navigation)


@router.callback_query(F.data == "nav:watchlist")
async def nav_watchlist(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer()
    if callback.message and callback.from_user:
        await navigation.go_to(state, ScreenMeta("watchlist", ["home", "watchlist"], {}))
        await _render_watchlist(callback.message, state, backend_api, navigation, callback.from_user.id)


@router.callback_query(F.data == "nav:back")
async def nav_back(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer()
    if not callback.message or not callback.from_user:
        return

    if await state.get_state() in {WalletFlow.waiting_wallet_input, WalletFlow.waiting_wallet_label}:
        await state.set_state(None)
        current = await navigation.current(state)
        data = await state.get_data()
        rename_address = data.get("rename_wallet_address")
        await state.update_data(awaiting_input=None, rename_wallet_address=None, rename_wallet_label=None)

        if rename_address:
            await _render_wallet(
                callback.message,
                state,
                backend_api,
                navigation,
                callback.from_user.id,
                str(rename_address),
                push_history=False,
            )
            return
        if current.screen_id == "watchlist":
            await _render_watchlist(callback.message, state, backend_api, navigation, callback.from_user.id)
            return
        await _render_home(callback.message, state, navigation)
        return

    previous = await navigation.go_back(state)
    if previous.screen_id == "watchlist":
        await _render_watchlist(callback.message, state, backend_api, navigation, callback.from_user.id)
        return
    if previous.screen_id == "wallet_detail" and previous.payload.get("address"):
        await _render_wallet(
            callback.message,
            state,
            backend_api,
            navigation,
            callback.from_user.id,
            previous.payload["address"],
            push_history=False,
        )
        return
    if previous.screen_id == "portfolio_detail" and previous.payload.get("address"):
        portfolio_view = (await state.get_data()).get("portfolio_view", {})
        await _render_portfolio(
            callback.message,
            state,
            backend_api,
            navigation,
            callback.from_user.id,
            previous.payload["address"],
            page=int(portfolio_view.get("page", 0)),
            push_history=False,
        )
        return
    if previous.screen_id == "transactions_detail" and previous.payload.get("address"):
        tx_view = (await state.get_data()).get("transactions_view", {})
        await _render_transactions(
            callback.message,
            state,
            backend_api,
            navigation,
            callback.from_user.id,
            previous.payload["address"],
            cursor=int(tx_view.get("cursor", 0)),
            prev_cursors=list(tx_view.get("prev_cursors", [])),
            hide_scam=bool(tx_view.get("hide_scam", False)),
            page_number=int(tx_view.get("page_number", 1)),
            push_history=False,
        )
        return
    await _render_home(callback.message, state, navigation)


@router.callback_query(F.data.in_({"action:add_watch", "action:quick_lookup"}))
async def start_wallet_input(
    callback: CallbackQuery,
    state: FSMContext,
    navigation: NavigationService,
) -> None:
    await callback.answer()
    if not callback.message:
        return

    mode = "add_watch" if callback.data == "action:add_watch" else "quick_lookup"
    callback_message = await _ensure_root_proxy_message(callback.message, state, navigation)
    await state.set_state(WalletFlow.waiting_wallet_input)
    await state.update_data(awaiting_input=mode, rename_wallet_address=None, rename_wallet_label=None)
    text, keyboard = input_screen(mode)
    await _send_text_root(callback_message, state, navigation, text, keyboard)


@router.callback_query(F.data.startswith("wallet:rename:"))
async def rename_wallet(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer()
    if not callback.message or not callback.from_user:
        return

    callback_message = await _ensure_root_proxy_message(callback.message, state, navigation)
    address = callback.data.split(":")[-1]
    try:
        summary = await backend_api.get_summary(callback.from_user.id, address)
        await state.set_state(WalletFlow.waiting_wallet_label)
        await state.update_data(
            awaiting_input="rename_label",
            rename_wallet_address=address,
            rename_wallet_label=summary.label or "",
        )
        text, keyboard = input_screen("rename_label", current_label=summary.label or "")
        await _send_text_root(callback_message, state, navigation, text, keyboard)
    except BackendError as error:
        await _show_error(callback_message, state, navigation, error)


@router.callback_query(F.data.startswith("wallet:open:"))
async def open_wallet(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer()
    if callback.message and callback.from_user:
        callback_message = await _ensure_root_proxy_message(callback.message, state, navigation)
        address = callback.data.split(":")[-1]
        try:
            await _render_wallet(
                callback_message,
                state,
                backend_api,
                navigation,
                callback.from_user.id,
                address,
                push_history=True,
            )
        except BackendError as error:
            await _show_error(callback_message, state, navigation, error)


@router.callback_query(F.data.startswith("wallet:refresh:"))
async def refresh_wallet(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer("Обновляю...")
    if callback.message and callback.from_user:
        callback_message = await _ensure_root_proxy_message(callback.message, state, navigation)
        address = callback.data.split(":")[-1]
        try:
            await _render_wallet(
                callback_message,
                state,
                backend_api,
                navigation,
                callback.from_user.id,
                address,
                push_history=False,
            )
        except BackendError as error:
            await _show_error(callback_message, state, navigation, error)


@router.callback_query(F.data.startswith("watch:add:"))
async def add_watch_from_detail(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer("Добавляю...")
    if callback.message and callback.from_user:
        callback_message = await _ensure_root_proxy_message(callback.message, state, navigation)
        address = callback.data.split(":")[-1]
        try:
            await backend_api.add_watch_wallet(callback.from_user.id, address)
            await _render_wallet(
                callback_message,
                state,
                backend_api,
                navigation,
                callback.from_user.id,
                address,
                push_history=False,
            )
        except BackendError as error:
            await _show_error(callback_message, state, navigation, error)


@router.callback_query(F.data.startswith("watch:remove:"))
async def remove_watch_from_detail(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer("Убираю...")
    if callback.message and callback.from_user:
        callback_message = await _ensure_root_proxy_message(callback.message, state, navigation)
        address = callback.data.split(":")[-1]
        try:
            await backend_api.remove_watch_wallet(callback.from_user.id, address)
            await _render_wallet(
                callback_message,
                state,
                backend_api,
                navigation,
                callback.from_user.id,
                address,
                push_history=False,
            )
        except BackendError as error:
            await _show_error(callback_message, state, navigation, error)


@router.callback_query(F.data.startswith("portfolio:open:"))
async def open_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer()
    if callback.message and callback.from_user:
        callback_message = await _ensure_root_proxy_message(callback.message, state, navigation)
        address = callback.data.split(":")[-1]
        try:
            await _render_portfolio(
                callback_message,
                state,
                backend_api,
                navigation,
                callback.from_user.id,
                address,
                page=0,
                push_history=True,
            )
        except BackendError as error:
            await _show_error(callback_message, state, navigation, error)


@router.callback_query(F.data.in_({"portfolio:next", "portfolio:prev"}))
async def paginate_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer()
    if callback.message and callback.from_user:
        callback_message = await _ensure_root_proxy_message(callback.message, state, navigation)
        data = await state.get_data()
        portfolio_view = data.get("portfolio_view", {})
        address = portfolio_view.get("address")
        page = int(portfolio_view.get("page", 0))
        if not address:
            await _render_home(callback_message, state, navigation)
            return

        next_page = page + 1 if callback.data == "portfolio:next" else max(0, page - 1)
        try:
            await _render_portfolio(
                callback_message,
                state,
                backend_api,
                navigation,
                callback.from_user.id,
                address,
                page=next_page,
                push_history=False,
            )
        except BackendError as error:
            await _show_error(callback_message, state, navigation, error)


@router.callback_query(F.data.startswith("tx:open:"))
async def open_transactions(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer()
    if callback.message and callback.from_user:
        callback_message = await _ensure_root_proxy_message(callback.message, state, navigation)
        address = callback.data.split(":")[-1]
        try:
            await _render_transactions(
                callback_message,
                state,
                backend_api,
                navigation,
                callback.from_user.id,
                address,
                cursor=0,
                prev_cursors=[],
                hide_scam=False,
                page_number=1,
                push_history=True,
            )
        except BackendError as error:
            await _show_error(callback_message, state, navigation, error)


@router.callback_query(F.data.in_({"tx:next", "tx:prev", "tx:toggle_scam"}))
async def mutate_transactions(
    callback: CallbackQuery,
    state: FSMContext,
    backend_api: BackendApi,
    navigation: NavigationService,
) -> None:
    await callback.answer()
    if callback.message and callback.from_user:
        callback_message = await _ensure_root_proxy_message(callback.message, state, navigation)
        data = await state.get_data()
        tx_view = data.get("transactions_view", {})
        address = tx_view.get("address")
        if not address:
            await _render_home(callback_message, state, navigation)
            return

        cursor = int(tx_view.get("cursor", 0))
        prev_cursors = list(tx_view.get("prev_cursors", []))
        next_cursor = tx_view.get("next_cursor")
        hide_scam = bool(tx_view.get("hide_scam", False))
        page_number = int(tx_view.get("page_number", 1))

        if callback.data == "tx:next" and next_cursor is not None:
            prev_cursors = [*prev_cursors, cursor]
            cursor = int(next_cursor)
            page_number += 1
        elif callback.data == "tx:prev" and prev_cursors:
            cursor = int(prev_cursors[-1])
            prev_cursors = prev_cursors[:-1]
            page_number = max(1, page_number - 1)
        elif callback.data == "tx:toggle_scam":
            hide_scam = not hide_scam
            cursor = 0
            prev_cursors = []
            page_number = 1

        try:
            await _render_transactions(
                callback_message,
                state,
                backend_api,
                navigation,
                callback.from_user.id,
                address,
                cursor=cursor,
                prev_cursors=prev_cursors,
                hide_scam=hide_scam,
                page_number=page_number,
                push_history=False,
            )
        except BackendError as error:
            await _show_error(callback_message, state, navigation, error)
