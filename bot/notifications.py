import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from aiogram import Bot

from bot.api_client import api_client
from bot.config import settings
from bot.naming import service_label as _svc_label
from bot.services.runtime import (
    ensure_backend_auth_session,
    get_notification_recipients,
    load_notification_balances,
    load_notification_changes,
    load_processed_transactions,
    save_notification_balances,
    save_notification_changes,
    save_processed_transactions,
    set_current_backend_auth_session,
    set_current_telegram_user_id,
)

logger = logging.getLogger(__name__)

STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USDD", "USD"}
OSCILLATION_WINDOW = timedelta(minutes=15)
BALANCE_BASED_SERVICES = {"okx", "okx_wallet"}

_previous_balances: Dict[str, Dict[str, float]] = {}
_change_history: Dict[str, Dict[str, List[Tuple[datetime, float, float]]]] = {}
_processed_transactions: Dict[str, str] = {}


def _is_balance_based_service(service_name: str) -> bool:
    service_lower = service_name.lower()
    for svc in BALANCE_BASED_SERVICES:
        if service_lower == svc or service_lower.startswith(f"{svc}_"):
            return True
    return False


def _format_amount(amount: float, coin: str) -> str:
    if coin in STABLECOINS:
        return f"{amount:,.2f}"
    if amount >= 1000:
        return f"{amount:,.2f}"
    if amount >= 1:
        return f"{amount:.4f}"
    return f"{amount:.8f}"


def _serialize_change_history() -> dict[str, dict[str, list[list[object]]]]:
    return {
        service: {
            coin: [[ts.isoformat(), old, new] for ts, old, new in history]
            for coin, history in coins.items()
        }
        for service, coins in _change_history.items()
    }


def _deserialize_change_history(
    payload: dict[str, dict[str, list[list[object]]]],
) -> Dict[str, Dict[str, List[Tuple[datetime, float, float]]]]:
    parsed: Dict[str, Dict[str, List[Tuple[datetime, float, float]]]] = {}
    for service, coins in payload.items():
        if not isinstance(coins, dict):
            continue
        parsed[service] = {}
        for coin, history in coins.items():
            if not isinstance(history, list):
                continue
            rows: List[Tuple[datetime, float, float]] = []
            for item in history:
                if not isinstance(item, list) or len(item) != 3:
                    continue
                try:
                    rows.append(
                        (
                            datetime.fromisoformat(str(item[0])),
                            float(item[1]),
                            float(item[2]),
                        )
                    )
                except (TypeError, ValueError):
                    continue
            parsed[service][coin] = rows
    return parsed


def _is_oscillation(
    service: str, coin: str, old_amount: float, new_amount: float
) -> bool:
    now = datetime.now()
    if service not in _change_history:
        _change_history[service] = {}
    if coin not in _change_history[service]:
        _change_history[service][coin] = []

    history = _change_history[service][coin]
    cutoff = now - OSCILLATION_WINDOW
    history[:] = [(ts, old, new) for ts, old, new in history if ts > cutoff]

    for _, prev_old, prev_new in history:
        if (
            abs(prev_old - new_amount) / max(prev_old, new_amount, 1) < 0.01
            and abs(prev_new - old_amount) / max(prev_new, old_amount, 1) < 0.01
        ):
            logger.info(
                "Oscillation detected for %s/%s: %s -> %s",
                service,
                coin,
                old_amount,
                new_amount,
            )
            return True

    history.append((now, old_amount, new_amount))
    return False


def _should_notify(coin: str, old_amount: float, new_amount: float) -> bool:
    diff = abs(new_amount - old_amount)
    if diff == 0:
        return False

    if coin in STABLECOINS:
        return diff >= settings.min_stablecoin_change

    max_balance = max(old_amount, new_amount)
    if max_balance > 0:
        percent_change = (diff / max_balance) * 100
        return percent_change >= settings.min_token_change_percent
    return False


def _detect_changes(
    service: str, new_assets: Dict[str, float]
) -> List[Tuple[str, str, float, float]]:
    changes: List[Tuple[str, str, float, float]] = []
    old_assets = _previous_balances.get(service, {})

    for coin in set(old_assets.keys()) | set(new_assets.keys()):
        old_amount = old_assets.get(coin, 0)
        new_amount = new_assets.get(coin, 0)

        if not _should_notify(coin, old_amount, new_amount):
            continue
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
    lines = [f"💰 <b>Balance changed on {_svc_label(service)}</b>", ""]

    inflows = [(c, o, n) for c, d, o, n in changes if d == "inflow"]
    outflows = [(c, o, n) for c, d, o, n in changes if d == "outflow"]

    if inflows:
        lines.append("📈 <b>Inflow:</b>")
        for coin, old_amt, new_amt in inflows:
            diff = new_amt - old_amt
            lines.append(f"  • +{_format_amount(diff, coin)} {coin}")
            lines.append(
                f"    <i>({_format_amount(old_amt, coin)} -> {_format_amount(new_amt, coin)})</i>"
            )

    if outflows:
        if inflows:
            lines.append("")
        lines.append("📉 <b>Outflow:</b>")
        for coin, old_amt, new_amt in outflows:
            diff = old_amt - new_amt
            lines.append(f"  • -{_format_amount(diff, coin)} {coin}")
            lines.append(
                f"    <i>({_format_amount(old_amt, coin)} -> {_format_amount(new_amt, coin)})</i>"
            )

    lines.append("")
    lines.append(f"🕐 {datetime.now().strftime('%H:%M:%S')}")
    return "\n".join(lines)


def _extract_current_assets(service_payload: dict[str, Any]) -> Dict[str, float]:
    current_assets: Dict[str, float] = {}

    for account in service_payload.get("accounts", []):
        for asset in account.get("assets", []):
            coin = asset.get("coin", "")
            amount = float(asset.get("amount", 0))
            if coin and amount > 0:
                current_assets[coin] = current_assets.get(coin, 0) + amount

    if current_assets:
        return current_assets

    for asset in service_payload.get("assets", []):
        coin = asset.get("coin", "")
        amount = float(asset.get("amount", 0))
        if coin and amount > 0:
            current_assets[coin] = current_assets.get(coin, 0) + amount

    return current_assets


async def check_and_notify(bot: Bot) -> None:
    global _previous_balances, _change_history

    try:
        for user_id in await get_notification_recipients():
            tg_token = set_current_telegram_user_id(user_id)
            backend_token = None
            try:
                backend_auth = await ensure_backend_auth_session(
                    telegram_user_id=user_id
                )
                backend_token = set_current_backend_auth_session(backend_auth)
                _previous_balances = await load_notification_balances(user_id)
                _change_history = _deserialize_change_history(
                    await load_notification_changes(user_id)
                )

                data = await api_client.get_balances(cached=True)
                services = data.get("services", [])

                for service_payload in services:
                    service_name = service_payload.get("service", "")
                    if not _is_balance_based_service(service_name):
                        continue

                    if not service_payload.get("actual", True):
                        logger.debug("Skipping %s: data is not actual", service_name)
                        continue

                    current_assets = _extract_current_assets(service_payload)
                    if service_name not in _previous_balances:
                        _previous_balances[service_name] = current_assets
                        continue

                    old_assets = _previous_balances.get(service_name, {})
                    if not current_assets and old_assets:
                        logger.warning(
                            "Skipping %s: empty balance received, keeping previous data",
                            service_name,
                        )
                        continue

                    changes = _detect_changes(service_name, current_assets)
                    if changes:
                        message = _format_notification(service_name, changes)
                        try:
                            await bot.send_message(user_id, message, parse_mode="HTML")
                        except Exception as exc:
                            logger.error(
                                "Failed to send notification to %s: %s", user_id, exc
                            )

                    _previous_balances[service_name] = current_assets

                await save_notification_balances(user_id, _previous_balances)
                await save_notification_changes(user_id, _serialize_change_history())
            finally:
                tg_token.var.reset(tg_token)
                if backend_token is not None:
                    backend_token.var.reset(backend_token)
    except Exception as exc:
        logger.error("Error checking balances for notifications: %s", exc)


async def notification_loop(bot: Bot, interval: int = 60) -> None:
    logger.info(
        "Starting balance notification loop for %s with %ss interval",
        BALANCE_BASED_SERVICES,
        interval,
    )
    await asyncio.sleep(10)

    while True:
        try:
            await check_and_notify(bot)
        except asyncio.CancelledError:
            logger.info("Notification loop cancelled")
            break
        except Exception as exc:
            logger.error("Notification loop error: %s", exc)
        await asyncio.sleep(interval)


def _format_transaction_notification(tx: Dict[str, Any]) -> str:
    tx_type = tx.get("tx_type", "unknown")
    service = tx.get("service", "unknown")
    currency = tx.get("currency", "UNKNOWN")
    amount = float(tx.get("amount", 0))
    network = tx.get("network") or "-"
    status = tx.get("status", "pending")
    txid = tx.get("txid") or "-"
    fee = tx.get("fee") or 0
    fee_currency = tx.get("fee_currency") or currency

    if tx_type == "deposit":
        icon = "📥"
        title = "Deposit"
        direction = "Received"
    else:
        icon = "📤"
        title = "Withdrawal"
        direction = "Sent"

    status_icon = {
        "ok": "✅",
        "pending": "⏳",
        "failed": "❌",
        "canceled": "🚫",
    }.get(status, "❓")
    status_text = {
        "ok": "Completed",
        "pending": "Pending",
        "failed": "Failed",
        "canceled": "Canceled",
    }.get(status, status)

    amount_str = _format_amount(amount, currency)
    lines = [
        f"{icon} <b>{title}</b> - {_svc_label(service)}",
        "",
        f"💰 {direction}: <b>{amount_str} {currency}</b>",
        f"🌐 Network: {network}",
        f"📊 Status: {status_icon} {status_text}",
    ]

    if fee and fee > 0:
        fee_str = (
            f"{float(fee):.2f}"
            if fee_currency in STABLECOINS
            else f"{float(fee):.8f}".rstrip("0").rstrip(".")
        )
        lines.append(f"💸 Fee: {fee_str} {fee_currency}")

    if txid and txid != "-":
        txid_short = f"{txid[:10]}...{txid[-8:]}" if len(txid) > 20 else txid
        lines.append(f"🔗 TX: <code>{txid_short}</code>")

    tx_timestamp = tx.get("tx_timestamp")
    if tx_timestamp:
        try:
            dt = (
                datetime.fromisoformat(tx_timestamp.replace("Z", "+00:00"))
                if isinstance(tx_timestamp, str)
                else tx_timestamp
            )
            lines.append(f"🕐 {dt.strftime('%d.%m.%Y %H:%M UTC')}")
        except Exception:
            pass

    return "\n".join(lines)


async def check_and_notify_transactions(bot: Bot) -> None:
    global _processed_transactions

    try:
        for user_id in await get_notification_recipients():
            tg_token = set_current_telegram_user_id(user_id)
            backend_token = None
            try:
                backend_auth = await ensure_backend_auth_session(
                    telegram_user_id=user_id
                )
                backend_token = set_current_backend_auth_session(backend_auth)
                _processed_transactions = await load_processed_transactions(user_id)

                try:
                    await api_client.refresh_transactions(since_hours=24)
                except Exception as exc:
                    logger.warning(
                        "Failed to refresh transactions for %s: %s", user_id, exc
                    )

                data = await api_client.get_transactions(status="ok", limit=50)
                transactions = data.get("transactions", [])

                for tx in transactions:
                    tx_id = tx.get("tx_id")
                    service = tx.get("service", "")
                    if not tx_id:
                        continue

                    tx_key = f"{service}_{tx_id}"
                    if tx_key in _processed_transactions:
                        continue

                    if tx.get("notified", False):
                        _processed_transactions[tx_key] = service
                        continue

                    message = _format_transaction_notification(tx)
                    try:
                        await bot.send_message(user_id, message, parse_mode="HTML")
                    except Exception as exc:
                        logger.error(
                            "Failed to send transaction notification to %s: %s",
                            user_id,
                            exc,
                        )

                    _processed_transactions[tx_key] = service
                    logger.info(
                        "Sent notification for user %s transaction %s", user_id, tx_key
                    )

                if len(_processed_transactions) > 1000:
                    items = list(_processed_transactions.items())
                    _processed_transactions = dict(items[-500:])

                await save_processed_transactions(user_id, _processed_transactions)
            finally:
                tg_token.var.reset(tg_token)
                if backend_token is not None:
                    backend_token.var.reset(backend_token)
    except Exception as exc:
        logger.error("Error checking transactions for notifications: %s", exc)


async def transaction_notification_loop(bot: Bot, interval: int = 120) -> None:
    logger.info("Starting transaction notification loop with %ss interval", interval)
    await asyncio.sleep(30)

    while True:
        try:
            await check_and_notify_transactions(bot)
        except asyncio.CancelledError:
            logger.info("Transaction notification loop cancelled")
            break
        except Exception as exc:
            logger.error("Transaction notification loop error: %s", exc)
        await asyncio.sleep(interval)


async def check_and_send_notification_events(bot: Bot) -> None:
    for user_id in await get_notification_recipients():
        tg_token = set_current_telegram_user_id(user_id)
        backend_token = None
        try:
            backend_auth = await ensure_backend_auth_session(telegram_user_id=user_id)
            backend_token = set_current_backend_auth_session(backend_auth)
            try:
                await api_client.generate_notifications(
                    kind="all",
                    poll_transactions=True,
                )
            except Exception as exc:
                logger.warning("Failed to generate notifications for %s: %s", user_id, exc)

            payload = await api_client.get_notification_events(status="pending", limit=20)
            events = payload.get("events") if isinstance(payload, dict) else []
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict):
                    continue
                event_id = int(event.get("id") or 0)
                title = str(event.get("title") or "Notification")
                body = str(event.get("body") or "")
                message = f"🔔 <b>{title}</b>"
                if body:
                    message += f"\n\n{body}"
                try:
                    await bot.send_message(user_id, message, parse_mode="HTML")
                    await api_client.mark_notification_event_sent(event_id)
                except Exception as exc:
                    logger.error("Failed to send notification event %s to %s: %s", event_id, user_id, exc)
                    if event_id:
                        try:
                            await api_client.mark_notification_event_failed(event_id, str(exc))
                        except Exception:
                            pass
        finally:
            tg_token.var.reset(tg_token)
            if backend_token is not None:
                backend_token.var.reset(backend_token)


async def notification_event_loop(bot: Bot, interval: int = 60) -> None:
    logger.info("Starting notification event loop with %ss interval", interval)
    await asyncio.sleep(15)
    while True:
        try:
            await check_and_send_notification_events(bot)
        except asyncio.CancelledError:
            logger.info("Notification event loop cancelled")
            break
        except Exception as exc:
            logger.error("Notification event loop error: %s", exc)
        await asyncio.sleep(interval)
