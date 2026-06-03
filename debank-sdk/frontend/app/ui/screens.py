from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.emoji_catalog import CHAIN_EMOJI_MAP
from app.services.backend_api import (
    TransactionTransfer,
    WalletPortfolio,
    WalletSummary,
    WalletTransactionsPage,
    WatchlistItem,
)
from app.services.navigation import ScreenMeta

EMOJI = {
    "wallet": "5769126056262898415",
    "people": "5870772616305839506",
    "info": "6028435952299413210",
    "add_text": "5771851822897566479",
    "back": "5893057118545646106",
    "loading": "5345906554510012647",
    "money": "5904462880941545555",
    "stats": "5870921681735781843",
    "growth": "5870930636742595124",
    "trash": "5870875489362513438",
    "home": "5873147866364514353",
    "cross": "5870657884844462243",
    "clock": "5983150113483134607",
    "box": "5884479287171485878",
    "apps": "5778672437122045013",
    "hidden": "6037243349675544634",
    "eye": "6037397706505195857",
    "check": "5870633910337015697",
    "edit": "5870676941614354370",
    "link": "5769289093221454192",
}


def message_emoji(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def chain_message_emoji(chain_id: str, fallback: str = "🔗") -> str:
    emoji_id = CHAIN_EMOJI_MAP.get(chain_id)
    if not emoji_id:
        return fallback
    return message_emoji(emoji_id, fallback)


def icon_button(text: str, callback_data: str, emoji_id: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data, icon_custom_emoji_id=emoji_id)


def shorten_address(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}"


def shorten_counterparty(address: str) -> str:
    if not address:
        return "unknown"
    return shorten_address(address) if address.startswith("0x") and len(address) > 12 else address


def money(value: float) -> str:
    return f"${value:,.2f}"


def message_title(emoji_id: str, fallback: str, title: str) -> str:
    return f"<b>{message_emoji(emoji_id, fallback)} {escape(title)}</b>"


def tx_status_label(status: int) -> str:
    if status == 1:
        return "Успешно"
    if status == 0:
        return "В процессе"
    return "Ошибка"


def tx_status_emoji(status: int) -> str:
    if status == 1:
        return EMOJI["check"]
    if status == 0:
        return EMOJI["loading"]
    return EMOJI["cross"]


def transfer_lines(items: list[TransactionTransfer], prefix: str) -> list[str]:
    lines: list[str] = []
    for item in items[:3]:
        raw_name = item.name or item.symbol or "Unknown token"
        raw_symbol = item.symbol or raw_name
        lines.append(
            " ".join(
                [
                    prefix,
                    f"<b>{escape(raw_symbol)}</b>",
                    f"({escape(raw_name)})",
                    f"· {item.amount:,.6f}",
                    f"· {money(item.usdValue)}",
                ]
            )
        )
    return lines


def home_screen() -> tuple[ScreenMeta, str, InlineKeyboardMarkup]:
    text = (
        f"{message_title(EMOJI['wallet'], '👛', 'DeBank Wallet Tracker')}\n\n"
        "Добавляйте кошельки в трек, проверяйте портфель и быстро открывайте последние транзакции "
        "в одном экране без команд."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                icon_button("Мои кошельки", "nav:watchlist", EMOJI["people"]),
                icon_button("Быстрый поиск", "action:quick_lookup", EMOJI["info"]),
            ]
        ]
    )
    return ScreenMeta("home", ["home"], {}), text, keyboard


def watchlist_screen(items: list[WatchlistItem]) -> tuple[ScreenMeta, str, InlineKeyboardMarkup]:
    if items:
        body = f"<b>В треке:</b> {len(items)}"
    else:
        body = "Трек пока пуст. Добавьте первый кошелёк через кнопку ниже."
    text = f"{message_title(EMOJI['people'], '👥', 'Мои кошельки')}\n\n{body}"

    rows = [
        [
            InlineKeyboardButton(
                text=item.label or shorten_address(item.address),
                callback_data=f"wallet:open:{item.address}",
                icon_custom_emoji_id=EMOJI["wallet"],
            )
        ]
        for item in items[:20]
    ]
    rows.extend(
        [
            [icon_button("Добавить кошелёк", "action:add_watch", EMOJI["add_text"])],
            [icon_button("Домой", "nav:home", EMOJI["home"])],
        ]
    )
    return ScreenMeta("watchlist", ["home", "watchlist"], {}), text, InlineKeyboardMarkup(inline_keyboard=rows)


def input_screen(
    mode: str,
    error_message: str | None = None,
    current_label: str | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    if mode == "rename_label":
        title = "Новое имя кошелька"
        body = "Отправьте новое название одним сообщением. Лимит: 64 символа."
        icon = EMOJI["edit"]
        fallback = "🖋"
    elif mode == "add_watch":
        title = "Добавление кошелька"
        body = "Отправьте адрес EVM- или Solana-кошелька одним сообщением. Бот сразу добавит его в трек."
        icon = EMOJI["add_text"]
        fallback = "✍"
    else:
        title = "Быстрый поиск"
        body = "Отправьте адрес EVM- или Solana-кошелька одним сообщением. Бот покажет портфель и транзакции."
        icon = EMOJI["info"]
        fallback = "ℹ"

    parts = [f"{message_title(icon, fallback, title)}\n\n{escape(body)}"]
    if current_label:
        parts.append(f"<b>Текущее имя:</b> {escape(current_label)}")
    if error_message:
        parts.append(
            f"<b>{message_emoji(EMOJI['cross'], '❌')} Ошибка:</b> {escape(error_message)}"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                icon_button("Назад", "nav:back", EMOJI["back"]),
                icon_button("Домой", "nav:home", EMOJI["home"]),
            ]
        ]
    )
    return "\n\n".join(parts), keyboard


def loading_screen(title: str, details: str) -> tuple[str, InlineKeyboardMarkup]:
    text = f"{message_title(EMOJI['loading'], '🔄', title)}\n\n{escape(details)}"
    return text, InlineKeyboardMarkup(inline_keyboard=[])


def wallet_screen(summary: WalletSummary) -> tuple[ScreenMeta, str, InlineKeyboardMarkup]:
    wallet_name = escape(summary.label or "Кошелёк без имени")
    top_tokens = [
        f"• <b>{escape(str(token['symbol']))}</b>: {money(float(token['amountUsd']))}"
        for token in summary.topTokens[:3]
    ]
    token_block = "\n".join(top_tokens) if top_tokens else "Нет данных по активам"

    text = (
        f"{message_title(EMOJI['wallet'], '👛', wallet_name)}\n"
        f"<code>{escape(summary.address)}</code>\n\n"
        f"<b>{message_emoji(EMOJI['money'], '🪙')} Баланс:</b> {money(summary.totalUsd)}\n"
        f"<b>{message_emoji(EMOJI['stats'], '📊')} Сетей:</b> {summary.chainCount}\n"
        f"<b>{message_emoji(EMOJI['wallet'], '👛')} Токенов:</b> {summary.tokenCount}\n"
        f"<b>{message_emoji(EMOJI['clock'], '⏰')} Транзакций:</b> {summary.recentTransactions}\n"
        f"<b>{message_emoji(EMOJI['cross'], '❌')} Scam:</b> {summary.scamTransactions}\n\n"
        f"<b>{message_emoji(EMOJI['growth'], '📈')} Топ активы</b>\n{token_block}"
    )

    watch_text = "Убрать из трека" if summary.tracked else "Добавить в трек"
    watch_icon = EMOJI["trash"] if summary.tracked else EMOJI["add_text"]
    watch_callback = f"watch:remove:{summary.address}" if summary.tracked else f"watch:add:{summary.address}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                icon_button("Портфель", f"portfolio:open:{summary.address}", EMOJI["box"]),
                icon_button("Транзакции", f"tx:open:{summary.address}", EMOJI["clock"]),
            ],
            [
                icon_button("Переименовать", f"wallet:rename:{summary.address}", EMOJI["edit"]),
                icon_button("Обновить", f"wallet:refresh:{summary.address}", EMOJI["loading"]),
            ],
            [icon_button(watch_text, watch_callback, watch_icon)],
            [
                icon_button("Назад", "nav:back", EMOJI["back"]),
                icon_button("Домой", "nav:home", EMOJI["home"]),
            ],
        ]
    )
    return ScreenMeta("wallet_detail", ["wallet_detail"], {"address": summary.address}), text, keyboard


def portfolio_screen(portfolio: WalletPortfolio) -> tuple[ScreenMeta, str, InlineKeyboardMarkup]:
    wallet_name = escape(portfolio.label or shorten_address(portfolio.address))
    chain_lines = [
        f"{chain_message_emoji(chain.id)} <b>{escape(chain.name)}</b> · {money(chain.totalUsd)} · {chain.tokenCount} ток."
        for chain in portfolio.chains[:5]
    ]
    token_lines = [
        f"{chain_message_emoji(token.chain)} <b>{escape(token.symbol)}</b> ({escape(token.name)}) · {money(token.amountUsd)}"
        for token in portfolio.tokens
    ]

    text = (
        f"{message_title(EMOJI['box'], '📦', f'Портфель {wallet_name}')}\n"
        f"<code>{escape(portfolio.address)}</code>\n\n"
        f"<b>{message_emoji(EMOJI['money'], '🪙')} Всего:</b> {money(portfolio.totalUsd)}\n"
        f"<b>{message_emoji(EMOJI['stats'], '📊')} Страница:</b> {portfolio.page + 1}\n"
        f"<b>{message_emoji(EMOJI['wallet'], '👛')} Токенов:</b> {portfolio.totalTokens}\n\n"
        f"<b>{message_emoji(EMOJI['apps'], '📦')} Сети</b>\n"
        f"{chr(10).join(chain_lines) if chain_lines else 'Нет данных по сетям'}\n\n"
        f"<b>{message_emoji(EMOJI['growth'], '📈')} Активы на странице</b>\n"
        f"{chr(10).join(token_lines) if token_lines else 'Нет токенов на этой странице'}"
    )

    rows: list[list[InlineKeyboardButton]] = []
    pager_row: list[InlineKeyboardButton] = []
    if portfolio.hasPrevPage:
        pager_row.append(icon_button("Назад", "portfolio:prev", EMOJI["back"]))
    if portfolio.hasNextPage:
        pager_row.append(icon_button("Вперёд", "portfolio:next", EMOJI["growth"]))
    if pager_row:
        rows.append(pager_row)
    rows.extend(
        [
            [icon_button("Транзакции", f"tx:open:{portfolio.address}", EMOJI["clock"])],
            [icon_button("К кошельку", f"wallet:open:{portfolio.address}", EMOJI["wallet"])],
            [
                icon_button("Назад", "nav:back", EMOJI["back"]),
                icon_button("Домой", "nav:home", EMOJI["home"]),
            ],
        ]
    )
    return ScreenMeta("portfolio_detail", ["portfolio_detail"], {"address": portfolio.address}), text, InlineKeyboardMarkup(inline_keyboard=rows)


def transactions_screen(
    page: WalletTransactionsPage,
    page_number: int,
    has_prev_page: bool,
) -> tuple[ScreenMeta, str, InlineKeyboardMarkup]:
    tx_blocks: list[str] = []
    for item in page.items:
        time_label = datetime.fromtimestamp(item.timeAt, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        scam_badge = f" {message_emoji(EMOJI['cross'], '❌')} scam" if item.isScam else ""
        tx_name = escape(item.txName or "Без названия")
        transfer_section = transfer_lines(item.receives, "⬇") + transfer_lines(item.sends, "⬆")
        transfers = "\n".join(transfer_section) if transfer_section else "• Нет детализации по токенам"

        tx_blocks.append(
            "\n".join(
                [
                    f"<b>{tx_name}</b>{scam_badge}",
                    f"{chain_message_emoji(item.chain)} {escape(item.chainName)}",
                    f"<b>{message_emoji(tx_status_emoji(item.status), '✅')} Статус:</b> {tx_status_label(item.status)}",
                    f"<b>{message_emoji(EMOJI['clock'], '⏰')} Время:</b> {time_label}",
                    f"<b>{message_emoji(EMOJI['link'], '🔗')} Контрагент:</b> <code>{escape(shorten_counterparty(item.otherAddr))}</code>",
                    f"<b>{message_emoji(EMOJI['money'], '🪙')} Поток USD:</b> +{money(item.receivedUsd)} / -{money(item.sentUsd)}",
                    transfers,
                ]
            )
        )

    tx_text = "\n\n──────────\n\n".join(tx_blocks) if tx_blocks else "Нет транзакций на этой странице."
    filter_label = "Скрыть scam" if not page.hideScam else "Показать scam"
    filter_icon = EMOJI["hidden"] if page.hideScam else EMOJI["eye"]
    filter_state = "scam скрыт" if page.hideScam else "scam виден"

    text = (
        f"{message_title(EMOJI['clock'], '⏰', 'Транзакции')}\n"
        f"<code>{escape(page.address)}</code>\n\n"
        f"<b>{message_emoji(EMOJI['stats'], '📊')} Страница:</b> {page_number}\n"
        f"<b>{message_emoji(filter_icon, '👁')} Фильтр:</b> {filter_state}\n\n"
        f"{tx_text}"
    )

    rows: list[list[InlineKeyboardButton]] = []
    pager_row: list[InlineKeyboardButton] = []
    if has_prev_page:
        pager_row.append(icon_button("Назад", "tx:prev", EMOJI["back"]))
    if page.nextCursor is not None:
        pager_row.append(icon_button("Вперёд", "tx:next", EMOJI["growth"]))
    if pager_row:
        rows.append(pager_row)
    rows.extend(
        [
            [icon_button(filter_label, "tx:toggle_scam", filter_icon)],
            [icon_button("Портфель", f"portfolio:open:{page.address}", EMOJI["box"])],
            [icon_button("К кошельку", f"wallet:open:{page.address}", EMOJI["wallet"])],
            [
                icon_button("Назад", "nav:back", EMOJI["back"]),
                icon_button("Домой", "nav:home", EMOJI["home"]),
            ],
        ]
    )
    return ScreenMeta("transactions_detail", ["transactions_detail"], {"address": page.address}), text, InlineKeyboardMarkup(inline_keyboard=rows)


def error_screen(message: str) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"{message_title(EMOJI['cross'], '❌', 'Не удалось выполнить действие')}\n\n"
        f"{escape(message)}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                icon_button("Назад", "nav:back", EMOJI["back"]),
                icon_button("Домой", "nav:home", EMOJI["home"]),
            ]
        ]
    )
    return text, keyboard
