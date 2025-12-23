import asyncio
import logging
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timedelta

from aiogram import Bot

from bot.config import ALLOWED_USERS, MIN_STABLECOIN_CHANGE, MIN_TOKEN_CHANGE_PERCENT
from bot.api_client import api_client

logger = logging.getLogger(__name__)

# Стейблкоины (1 токен ≈ $1)
STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USDD", "USD"}

# Хранилище предыдущих балансов: {service: {coin: amount}}
_previous_balances: Dict[str, Dict[str, float]] = {}

# История изменений для фильтрации шума: {service: {coin: [(timestamp, old, new), ...]}}
_change_history: Dict[str, Dict[str, List[Tuple[datetime, float, float]]]] = {}

# Время для определения "колебаний" (если изменение туда-обратно за это время - игнорируем)
OSCILLATION_WINDOW = timedelta(minutes=15)

# Хранилище обработанных транзакций (tx_id: service)
_processed_transactions: Dict[str, str] = {}

# Биржи, для которых используется подход через изменение баланса (без API транзакций)
# Для остальных бирж уведомления приходят через транзакции
BALANCE_BASED_SERVICES = {"okx"}


def _format_amount(amount: float, coin: str) -> str:
    """Форматирует количество токенов"""
    if coin in STABLECOINS:
        return f"{amount:,.2f}"
    elif amount >= 1000:
        return f"{amount:,.2f}"
    elif amount >= 1:
        return f"{amount:.4f}"
    else:
        return f"{amount:.8f}"


def _is_oscillation(
    service: str, coin: str, old_amount: float, new_amount: float
) -> bool:
    """
    Проверяет, является ли изменение "колебанием" (туда-обратно).
    Возвращает True если нужно игнорировать это изменение.
    """
    now = datetime.now()

    # Инициализируем историю если нужно
    if service not in _change_history:
        _change_history[service] = {}
    if coin not in _change_history[service]:
        _change_history[service][coin] = []

    history = _change_history[service][coin]

    # Очищаем старые записи
    cutoff = now - OSCILLATION_WINDOW
    history[:] = [(ts, old, new) for ts, old, new in history if ts > cutoff]

    # Проверяем, было ли обратное изменение недавно
    # Если раньше было new_amount -> old_amount, а сейчас old_amount -> new_amount - это колебание
    for ts, prev_old, prev_new in history:
        # Проверяем обратное изменение с допуском 1%
        if (
            abs(prev_old - new_amount) / max(prev_old, new_amount, 1) < 0.01
            and abs(prev_new - old_amount) / max(prev_new, old_amount, 1) < 0.01
        ):
            logger.info(
                f"Oscillation detected for {service}/{coin}: {old_amount} -> {new_amount} "
                f"(reverse of {prev_old} -> {prev_new})"
            )
            return True

    # Записываем текущее изменение
    history.append((now, old_amount, new_amount))

    return False


def _should_notify(coin: str, old_amount: float, new_amount: float) -> bool:
    """Определяет, нужно ли отправлять уведомление об изменении"""
    diff = abs(new_amount - old_amount)

    if diff == 0:
        return False

    # Для стейблкоинов - минимум $1 изменение
    if coin in STABLECOINS:
        return diff >= MIN_STABLECOIN_CHANGE

    # Для других токенов - минимум X% от текущего или предыдущего баланса
    max_balance = max(old_amount, new_amount)
    if max_balance > 0:
        percent_change = (diff / max_balance) * 100  # в процентах
        return percent_change >= MIN_TOKEN_CHANGE_PERCENT

    return False


def _detect_changes(
    service: str, new_assets: Dict[str, float]
) -> List[Tuple[str, str, float, float]]:
    """
    Определяет изменения в балансах
    Возвращает список: [(coin, direction, old_amount, new_amount), ...]
    direction: "inflow" или "outflow"
    """
    changes = []
    old_assets = _previous_balances.get(service, {})

    all_coins = set(old_assets.keys()) | set(new_assets.keys())

    for coin in all_coins:
        old_amount = old_assets.get(coin, 0)
        new_amount = new_assets.get(coin, 0)

        if not _should_notify(coin, old_amount, new_amount):
            continue

        # Проверяем, не является ли это колебанием (туда-обратно)
        if _is_oscillation(service, coin, old_amount, new_amount):
            continue

        if new_amount > old_amount:
            changes.append((coin, "inflow", old_amount, new_amount))
        elif new_amount < old_amount:
            changes.append((coin, "outflow", old_amount, new_amount))

    return changes


def _format_notification(
    service: str, changes: List[Tuple[str, str, float, float]]
) -> str:
    """Форматирует сообщение уведомления"""
    lines = [f"💰 <b>Изменение баланса на {service}</b>", ""]

    inflows = [(c, o, n) for c, d, o, n in changes if d == "inflow"]
    outflows = [(c, o, n) for c, d, o, n in changes if d == "outflow"]

    if inflows:
        lines.append("📈 <b>Приток:</b>")
        for coin, old_amt, new_amt in inflows:
            diff = new_amt - old_amt
            lines.append(f"  • +{_format_amount(diff, coin)} {coin}")
            lines.append(
                f"    <i>({_format_amount(old_amt, coin)} → {_format_amount(new_amt, coin)})</i>"
            )

    if outflows:
        if inflows:
            lines.append("")
        lines.append("📉 <b>Отток:</b>")
        for coin, old_amt, new_amt in outflows:
            diff = old_amt - new_amt
            lines.append(f"  • -{_format_amount(diff, coin)} {coin}")
            lines.append(
                f"    <i>({_format_amount(old_amt, coin)} → {_format_amount(new_amt, coin)})</i>"
            )

    lines.append("")
    lines.append(f"🕐 {datetime.now().strftime('%H:%M:%S')}")

    return "\n".join(lines)


async def check_and_notify(bot: Bot) -> None:
    """
    Проверяет изменения балансов и отправляет уведомления.
    Используется ТОЛЬКО для бирж из BALANCE_BASED_SERVICES (OKX и др.),
    у которых нет API для получения истории транзакций.
    Для остальных бирж уведомления приходят через transaction_notification_loop.
    """
    global _previous_balances

    try:
        data = await api_client.get_balances(cached=True)
        services = data.get("services", [])

        for svc in services:
            service_name = svc.get("service", "")

            # Обрабатываем ТОЛЬКО биржи из BALANCE_BASED_SERVICES
            if service_name.lower() not in BALANCE_BASED_SERVICES:
                continue

            # Пропускаем если данные неактуальные (ошибка API, fallback)
            is_actual = svc.get("actual", True)
            if not is_actual:
                logger.debug(
                    f"Skipping {service_name}: data is not actual (API error/fallback)"
                )
                continue

            # Собираем текущие балансы по количеству токенов
            current_assets: Dict[str, float] = {}

            # Из accounts
            for acc in svc.get("accounts", []):
                for asset in acc.get("assets", []):
                    coin = asset.get("coin", "")
                    amount = float(asset.get("amount", 0))
                    if coin and amount > 0:
                        current_assets[coin] = current_assets.get(coin, 0) + amount

            # Из assets (legacy)
            if not current_assets:
                for asset in svc.get("assets", []):
                    coin = asset.get("coin", "")
                    amount = float(asset.get("amount", 0))
                    if coin and amount > 0:
                        current_assets[coin] = current_assets.get(coin, 0) + amount

            # Пропускаем первый запуск (инициализация)
            if service_name not in _previous_balances:
                _previous_balances[service_name] = current_assets
                continue

            # Защита от фиктивных изменений: если текущий баланс пустой,
            # но предыдущий был непустой - это скорее всего ошибка API
            old_assets = _previous_balances.get(service_name, {})
            if not current_assets and old_assets:
                logger.warning(
                    f"Skipping {service_name}: empty balance received, keeping previous data"
                )
                continue

            # Определяем изменения
            changes = _detect_changes(service_name, current_assets)

            if changes:
                message = _format_notification(service_name, changes)

                # Отправляем уведомление всем разрешённым пользователям
                for user_id in ALLOWED_USERS:
                    try:
                        await bot.send_message(user_id, message, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Failed to send notification to {user_id}: {e}")

            # Обновляем сохранённые балансы
            _previous_balances[service_name] = current_assets

    except Exception as e:
        logger.error(f"Error checking balances for notifications: {e}")


async def notification_loop(bot: Bot, interval: int = 60) -> None:
    """
    Фоновый цикл проверки изменений балансов.
    Используется ТОЛЬКО для бирж из BALANCE_BASED_SERVICES (OKX).
    """
    logger.info(
        f"Starting balance notification loop for {BALANCE_BASED_SERVICES} with {interval}s interval"
    )

    # Начальная задержка для загрузки данных
    await asyncio.sleep(10)

    while True:
        try:
            await check_and_notify(bot)
        except asyncio.CancelledError:
            logger.info("Notification loop cancelled")
            break
        except Exception as e:
            logger.error(f"Notification loop error: {e}")

        await asyncio.sleep(interval)


# ==================== Transaction Notifications ====================


def _format_transaction_notification(tx: Dict) -> str:
    """Форматирует уведомление о транзакции"""
    tx_type = tx.get("tx_type", "unknown")
    service = tx.get("service", "unknown")
    currency = tx.get("currency", "UNKNOWN")
    amount = float(tx.get("amount", 0))
    network = tx.get("network") or "—"
    status = tx.get("status", "pending")
    txid = tx.get("txid") or "—"
    fee = tx.get("fee") or 0
    fee_currency = tx.get("fee_currency") or currency

    # Иконки и заголовок
    if tx_type == "deposit":
        icon = "📥"
        title = "Ввод средств"
        direction = "Получено"
    else:
        icon = "📤"
        title = "Вывод средств"
        direction = "Отправлено"

    # Статус
    status_icons = {
        "ok": "✅",
        "pending": "⏳",
        "failed": "❌",
        "canceled": "🚫",
    }
    status_icon = status_icons.get(status, "❓")
    status_text = {
        "ok": "Завершено",
        "pending": "В обработке",
        "failed": "Ошибка",
        "canceled": "Отменено",
    }.get(status, status)

    # Форматирование суммы
    if currency in STABLECOINS:
        amount_str = f"{amount:,.2f}"
    elif amount >= 1000:
        amount_str = f"{amount:,.2f}"
    elif amount >= 1:
        amount_str = f"{amount:.4f}"
    else:
        amount_str = f"{amount:.8f}"

    lines = [
        f"{icon} <b>{title}</b> — {service.upper()}",
        "",
        f"💰 {direction}: <b>{amount_str} {currency}</b>",
        f"🌐 Сеть: {network}",
        f"📊 Статус: {status_icon} {status_text}",
    ]

    if fee and fee > 0:
        if fee_currency in STABLECOINS:
            fee_str = f"{fee:.2f}"
        else:
            fee_str = f"{fee:.8f}".rstrip("0").rstrip(".")
        lines.append(f"💸 Комиссия: {fee_str} {fee_currency}")

    if txid and txid != "—":
        # Сокращаем длинный txid
        if len(txid) > 20:
            txid_short = f"{txid[:10]}...{txid[-8:]}"
        else:
            txid_short = txid
        lines.append(f"🔗 TX: <code>{txid_short}</code>")

    # Время транзакции
    tx_timestamp = tx.get("tx_timestamp")
    if tx_timestamp:
        try:
            if isinstance(tx_timestamp, str):
                dt = datetime.fromisoformat(tx_timestamp.replace("Z", "+00:00"))
            else:
                dt = tx_timestamp
            time_str = dt.strftime("%d.%m.%Y %H:%M UTC")
            lines.append(f"🕐 {time_str}")
        except:
            pass

    return "\n".join(lines)


async def check_and_notify_transactions(bot: Bot) -> None:
    """Проверяет новые транзакции и отправляет уведомления"""
    global _processed_transactions

    try:
        # Сначала обновляем транзакции с бирж
        try:
            await api_client.refresh_transactions(since_hours=24)
        except Exception as e:
            logger.warning(f"Failed to refresh transactions: {e}")

        # Получаем последние транзакции
        data = await api_client.get_transactions(status="ok", limit=50)
        transactions = data.get("transactions", [])

        for tx in transactions:
            tx_id = tx.get("tx_id")
            service = tx.get("service", "")

            if not tx_id:
                continue

            # Уникальный ключ для транзакции
            tx_key = f"{service}_{tx_id}"

            # Пропускаем уже обработанные
            if tx_key in _processed_transactions:
                continue

            # Проверяем, было ли уже отправлено уведомление (по флагу notified)
            if tx.get("notified", False):
                _processed_transactions[tx_key] = service
                continue

            # Отправляем уведомление
            message = _format_transaction_notification(tx)

            for user_id in ALLOWED_USERS:
                try:
                    await bot.send_message(user_id, message, parse_mode="HTML")
                except Exception as e:
                    logger.error(
                        f"Failed to send transaction notification to {user_id}: {e}"
                    )

            # Помечаем как обработанную
            _processed_transactions[tx_key] = service

            logger.info(f"Sent notification for transaction {tx_key}")

        # Очищаем старые записи (храним только последние 1000)
        if len(_processed_transactions) > 1000:
            # Оставляем последние 500
            items = list(_processed_transactions.items())
            _processed_transactions = dict(items[-500:])

    except Exception as e:
        logger.error(f"Error checking transactions for notifications: {e}")


async def transaction_notification_loop(bot: Bot, interval: int = 120) -> None:
    """Фоновый цикл проверки новых транзакций"""
    logger.info(f"Starting transaction notification loop with {interval}s interval")

    # Начальная задержка
    await asyncio.sleep(30)

    while True:
        try:
            await check_and_notify_transactions(bot)
        except asyncio.CancelledError:
            logger.info("Transaction notification loop cancelled")
            break
        except Exception as e:
            logger.error(f"Transaction notification loop error: {e}")

        await asyncio.sleep(interval)
