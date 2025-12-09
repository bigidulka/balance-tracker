import asyncio
import logging
from typing import Dict, Optional, List, Tuple
from datetime import datetime

from aiogram import Bot

from bot.config import ALLOWED_USERS, MIN_STABLECOIN_CHANGE, MIN_TOKEN_CHANGE_PERCENT
from bot.api_client import api_client

logger = logging.getLogger(__name__)

# Стейблкоины (1 токен ≈ $1)
STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USDD", "USD"}

# Хранилище предыдущих балансов: {service: {coin: amount}}
_previous_balances: Dict[str, Dict[str, float]] = {}


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
    """Проверяет изменения балансов и отправляет уведомления"""
    global _previous_balances

    try:
        data = await api_client.get_balances(cached=True)
        services = data.get("services", [])

        for svc in services:
            service_name = svc.get("service", "")

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
    """Фоновый цикл проверки изменений балансов"""
    logger.info(f"Starting notification loop with {interval}s interval")

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
