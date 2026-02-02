from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from typing import Dict, Any, List
from datetime import datetime, timezone
import math

from bot.config import ALLOWED_USERS, HIDE_SMALL_BALANCE_THRESHOLD
from bot.api_client import api_client
from bot.keyboards import (
    main_menu_kb,
    spot_exchanges_kb,
    futures_exchanges_kb,
    exchange_detail_kb,
    settings_kb,
    dex_kb,
    assets_pagination_kb,
)

router = Router()

# User settings storage
user_settings: Dict[int, Dict[str, Any]] = {}

# Cache for balance data
balance_cache: Dict[str, Any] = {}


def get_user_settings(user_id: int) -> Dict[str, Any]:
    if user_id not in user_settings:
        user_settings[user_id] = {"hide_small": False}
    return user_settings[user_id]


def format_usd(value: float) -> str:
    if value >= 1000:
        return f"${value:,.2f}"
    return f"${value:.2f}"


def format_timestamp(data: Dict[str, Any]) -> str:
    """Format data freshness timestamp"""
    # Get the latest update time from services
    services = data.get("services", [])
    if not services:
        return "No data"

    latest_time = None
    for svc in services:
        updated_at = svc.get("updated_at")
        if updated_at:
            try:
                if isinstance(updated_at, str):
                    # Parse ISO format
                    dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                else:
                    dt = updated_at
                
                # We prefer the latest update time to show when the last refresh happened
                if latest_time is None or dt > latest_time:
                    latest_time = dt
            except Exception:
                pass

    if latest_time is None:
        return "Unknown"

    # Calculate age
    now = datetime.now(timezone.utc)
    if latest_time.tzinfo is None:
        latest_time = latest_time.replace(tzinfo=timezone.utc)

    age = now - latest_time
    minutes = int(age.total_seconds() / 60)

    if minutes < 1:
        return "🟢 Updated just now"
    elif minutes < 5:
        return f"🟢 Updated {minutes}m ago"
    elif minutes < 15:
        return f"🟡 Updated {minutes}m ago"
    else:
        return f"🔴 Updated {minutes}m ago"


def filter_assets(assets: List[Dict], hide_small: bool) -> List[Dict]:
    if not hide_small:
        return assets
    return [a for a in assets if a.get("value_usd", 0) >= HIDE_SMALL_BALANCE_THRESHOLD]


async def get_balance_data() -> Dict[str, Any]:
    global balance_cache
    try:
        balance_cache = await api_client.get_balances(cached=True)
    except Exception as e:
        if not balance_cache:
            raise e
    return balance_cache


def parse_balances(data: Dict[str, Any]) -> Dict[str, Any]:
    services = data.get("services", [])

    spot_total = 0.0
    futures_total = 0.0
    dex_total = 0.0
    exchanges_count = 0

    spot_exchanges = {}
    futures_exchanges = {}
    dex_wallets = {}

    for svc in services:
        name = svc.get("service", "")
        accounts = svc.get("accounts", [])
        # For cached data, assets are at service level, not in accounts
        service_assets = svc.get("assets", [])
        service_total = svc.get("total_usd", 0)

        if name.startswith("okx_wallet"):
            # DEX wallet - use service level data
            if accounts:
                for acc in accounts:
                    if acc.get("account_type") == "spot":
                        dex_total += acc.get("total_usd", 0)
                        dex_wallets[name] = acc
            else:
                # Cached data - use service level
                dex_total += service_total
                dex_wallets[name] = {
                    "account_type": "spot",
                    "assets": service_assets,
                    "total_usd": service_total,
                }
        else:
            exchanges_count += 1
            if accounts:
                # Full data with accounts breakdown
                for acc in accounts:
                    acc_type = acc.get("account_type")
                    if acc_type == "spot":
                        spot_total += acc.get("total_usd", 0)
                        if acc.get("total_usd", 0) > 0 or acc.get("assets"):
                            spot_exchanges[name] = acc
                    elif acc_type == "futures":
                        futures_total += acc.get("total_usd", 0)
                        if acc.get("total_usd", 0) > 0 or acc.get("assets"):
                            futures_exchanges[name] = acc
            else:
                # No accounts - treat as spot (legacy data without account breakdown)
                spot_total += service_total
                if service_total > 0 or service_assets:
                    spot_exchanges[name] = {
                        "account_type": "spot",
                        "assets": service_assets,
                        "total_usd": service_total,
                    }

    return {
        "total": spot_total + futures_total + dex_total,
        "spot_total": spot_total,
        "futures_total": futures_total,
        "dex_total": dex_total,
        "exchanges_count": exchanges_count,
        "spot_exchanges": spot_exchanges,
        "futures_exchanges": futures_exchanges,
        "dex_wallets": dex_wallets,
    }


@router.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id not in ALLOWED_USERS:
        await message.answer("Access denied")
        return

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        freshness = format_timestamp(data)

        text = (
            f"📊 Balance Overview\n"
            f"{'='*30}\n\n"
            f"💰 Total: {format_usd(parsed['total'])}\n\n"
            f"📈 Exchanges: {parsed['exchanges_count']}\n"
            f"• Spot: {format_usd(parsed['spot_total'])}\n"
            f"• Futures: {format_usd(parsed['futures_total'])}\n"
            f"🔗 DEX Wallet: {format_usd(parsed['dex_total'])}\n\n"
            f"⏱ {freshness}"
        )

        await message.answer(text, reply_markup=main_menu_kb())
    except Exception as e:
        await message.answer(f"❌ Error: {str(e)}", reply_markup=main_menu_kb())


@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        freshness = format_timestamp(data)

        text = (
            f"📊 Balance Overview\n"
            f"{'='*30}\n\n"
            f"💰 Total: {format_usd(parsed['total'])}\n\n"
            f"📈 Exchanges: {parsed['exchanges_count']}\n"
            f"• Spot: {format_usd(parsed['spot_total'])}\n"
            f"• Futures: {format_usd(parsed['futures_total'])}\n"
            f"🔗 DEX Wallet: {format_usd(parsed['dex_total'])}\n\n"
            f"⏱ {freshness}"
        )

        await callback.message.edit_text(text, reply_markup=main_menu_kb())
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Error: {str(e)}", reply_markup=main_menu_kb()
        )

    await callback.answer()


@router.callback_query(F.data == "refresh")
async def refresh_data(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    await callback.answer("🔄 Refreshing from cache...")

    try:
        global balance_cache
        balance_cache = await api_client.get_balances(cached=True)
        parsed = parse_balances(balance_cache)
        freshness = format_timestamp(balance_cache)

        text = (
            f"📊 Balance Overview\n"
            f"{'='*30}\n\n"
            f"💰 Total: {format_usd(parsed['total'])}\n\n"
            f"📈 Exchanges: {parsed['exchanges_count']}\n"
            f"• Spot: {format_usd(parsed['spot_total'])}\n"
            f"• Futures: {format_usd(parsed['futures_total'])}\n"
            f"🔗 DEX Wallet: {format_usd(parsed['dex_total'])}\n\n"
            f"⏱ {freshness}"
        )

        await callback.message.edit_text(text, reply_markup=main_menu_kb())
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Error: {str(e)}", reply_markup=main_menu_kb()
        )


@router.callback_query(F.data == "spot")
async def show_spot(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        settings = get_user_settings(callback.from_user.id)

        lines = [
            f"Spot Balances",
            f"{'='*30}",
            f"Total: {format_usd(parsed['spot_total'])}",
            "",
        ]

        exchanges = []
        for name, acc in sorted(
            parsed["spot_exchanges"].items(),
            key=lambda x: x[1].get("total_usd", 0),
            reverse=True,
        ):
            total = acc.get("total_usd", 0)
            if settings["hide_small"] and total < HIDE_SMALL_BALANCE_THRESHOLD:
                continue
            lines.append(f"{name}: {format_usd(total)}")
            exchanges.append(name)

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=spot_exchanges_kb(exchanges)
        )
    except Exception as e:
        await callback.message.edit_text(
            f"Error: {str(e)}", reply_markup=main_menu_kb()
        )

    await callback.answer()


@router.callback_query(F.data.startswith("spot_page_"))
async def spot_page(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    page = int(callback.data.split("_")[-1])

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        settings = get_user_settings(callback.from_user.id)

        lines = [
            f"Spot Balances",
            f"{'='*30}",
            f"Total: {format_usd(parsed['spot_total'])}",
            "",
        ]

        exchanges = []
        for name, acc in sorted(
            parsed["spot_exchanges"].items(),
            key=lambda x: x[1].get("total_usd", 0),
            reverse=True,
        ):
            total = acc.get("total_usd", 0)
            if settings["hide_small"] and total < HIDE_SMALL_BALANCE_THRESHOLD:
                continue
            lines.append(f"{name}: {format_usd(total)}")
            exchanges.append(name)

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=spot_exchanges_kb(exchanges, page)
        )
    except Exception as e:
        await callback.message.edit_text(
            f"Error: {str(e)}", reply_markup=main_menu_kb()
        )

    await callback.answer()


@router.callback_query(F.data.startswith("spot_") & ~F.data.startswith("spot_page_"))
async def spot_exchange_detail(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    exchange = callback.data.replace("spot_", "")

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        settings = get_user_settings(callback.from_user.id)

        acc = parsed["spot_exchanges"].get(exchange, {})
        assets = acc.get("assets", [])
        assets = sorted(assets, key=lambda x: x.get("value_usd", 0), reverse=True)
        assets = filter_assets(assets, settings["hide_small"])

        lines = [
            f"{exchange} - Spot",
            f"{'='*30}",
            f"Total: {format_usd(acc.get('total_usd', 0))}",
            "",
        ]

        for asset in assets[:20]:
            coin = asset.get("coin", "?")
            amount = asset.get("amount", 0)
            value = asset.get("value_usd", 0)
            lines.append(f"{coin}: {amount:.6g} ({format_usd(value)})")

        if len(assets) > 20:
            lines.append(f"\n... and {len(assets) - 20} more")

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=exchange_detail_kb("spot")
        )
    except Exception as e:
        await callback.message.edit_text(
            f"Error: {str(e)}", reply_markup=main_menu_kb()
        )

    await callback.answer()


@router.callback_query(F.data == "back_spot")
async def back_to_spot(callback: CallbackQuery):
    await show_spot(callback)


@router.callback_query(F.data == "futures")
async def show_futures(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        settings = get_user_settings(callback.from_user.id)

        lines = [
            f"Futures Balances",
            f"{'='*30}",
            f"Total: {format_usd(parsed['futures_total'])}",
            "",
        ]

        exchanges = []
        for name, acc in sorted(
            parsed["futures_exchanges"].items(),
            key=lambda x: x[1].get("total_usd", 0),
            reverse=True,
        ):
            total = acc.get("total_usd", 0)
            if settings["hide_small"] and total < HIDE_SMALL_BALANCE_THRESHOLD:
                continue
            lines.append(f"{name}: {format_usd(total)}")
            exchanges.append(name)

        if not exchanges:
            lines.append("No futures balances")

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=futures_exchanges_kb(exchanges)
        )
    except Exception as e:
        await callback.message.edit_text(
            f"Error: {str(e)}", reply_markup=main_menu_kb()
        )

    await callback.answer()


@router.callback_query(F.data.startswith("futures_page_"))
async def futures_page(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    page = int(callback.data.split("_")[-1])

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        settings = get_user_settings(callback.from_user.id)

        lines = [
            f"Futures Balances",
            f"{'='*30}",
            f"Total: {format_usd(parsed['futures_total'])}",
            "",
        ]

        exchanges = []
        for name, acc in sorted(
            parsed["futures_exchanges"].items(),
            key=lambda x: x[1].get("total_usd", 0),
            reverse=True,
        ):
            total = acc.get("total_usd", 0)
            if settings["hide_small"] and total < HIDE_SMALL_BALANCE_THRESHOLD:
                continue
            lines.append(f"{name}: {format_usd(total)}")
            exchanges.append(name)

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=futures_exchanges_kb(exchanges, page)
        )
    except Exception as e:
        await callback.message.edit_text(
            f"Error: {str(e)}", reply_markup=main_menu_kb()
        )

    await callback.answer()


@router.callback_query(
    F.data.startswith("futures_") & ~F.data.startswith("futures_page_")
)
async def futures_exchange_detail(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    exchange = callback.data.replace("futures_", "")

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        settings = get_user_settings(callback.from_user.id)

        acc = parsed["futures_exchanges"].get(exchange, {})
        assets = acc.get("assets", [])
        assets = sorted(assets, key=lambda x: x.get("value_usd", 0), reverse=True)
        assets = filter_assets(assets, settings["hide_small"])

        lines = [
            f"{exchange} - Futures",
            f"{'='*30}",
            f"Total: {format_usd(acc.get('total_usd', 0))}",
            "",
        ]

        for asset in assets[:20]:
            coin = asset.get("coin", "?")
            amount = asset.get("amount", 0)
            value = asset.get("value_usd", 0)
            lines.append(f"{coin}: {amount:.6g} ({format_usd(value)})")

        if len(assets) > 20:
            lines.append(f"\n... and {len(assets) - 20} more")

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=exchange_detail_kb("futures")
        )
    except Exception as e:
        await callback.message.edit_text(
            f"Error: {str(e)}", reply_markup=main_menu_kb()
        )

    await callback.answer()


@router.callback_query(F.data == "back_futures")
async def back_to_futures(callback: CallbackQuery):
    await show_futures(callback)


@router.callback_query(F.data == "dex")
async def show_dex(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    await show_dex_page(callback, 0)


@router.callback_query(F.data.startswith("dex_page_"))
async def dex_page_handler(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    page = int(callback.data.split("_")[-1])
    await show_dex_page(callback, page)


async def show_dex_page(callback: CallbackQuery, page: int):
    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        settings = get_user_settings(callback.from_user.id)

        all_assets = []
        for name, acc in parsed["dex_wallets"].items():
            all_assets.extend(acc.get("assets", []))

        all_assets = sorted(
            all_assets, key=lambda x: x.get("value_usd", 0), reverse=True
        )
        all_assets = filter_assets(all_assets, settings["hide_small"])

        per_page = 20
        total_pages = max(1, math.ceil(len(all_assets) / per_page))
        start = page * per_page
        end = start + per_page
        page_assets = all_assets[start:end]

        lines = [
            f"DEX Wallet",
            f"{'='*30}",
            f"Total: {format_usd(parsed['dex_total'])}",
            "",
        ]

        for asset in page_assets:
            coin = asset.get("coin", "?")
            amount = asset.get("amount", 0)
            value = asset.get("value_usd", 0)
            lines.append(f"{coin}: {amount:.6g} ({format_usd(value)})")

        if not page_assets:
            lines.append("No assets")

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=dex_kb(page, total_pages)
        )
    except Exception as e:
        await callback.message.edit_text(
            f"Error: {str(e)}", reply_markup=main_menu_kb()
        )

    await callback.answer()


@router.callback_query(F.data == "settings")
async def show_settings(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    settings = get_user_settings(callback.from_user.id)

    text = (
        f"Settings\n"
        f"{'='*30}\n\n"
        f"Hide balances less than ${HIDE_SMALL_BALANCE_THRESHOLD:.2f}"
    )

    await callback.message.edit_text(
        text, reply_markup=settings_kb(settings["hide_small"])
    )
    await callback.answer()


@router.callback_query(F.data == "toggle_hide_small")
async def toggle_hide_small(callback: CallbackQuery):
    if callback.from_user.id not in ALLOWED_USERS:
        await callback.answer("Access denied")
        return

    settings = get_user_settings(callback.from_user.id)
    settings["hide_small"] = not settings["hide_small"]

    text = (
        f"Settings\n"
        f"{'='*30}\n\n"
        f"Hide balances less than ${HIDE_SMALL_BALANCE_THRESHOLD:.2f}"
    )

    await callback.message.edit_text(
        text, reply_markup=settings_kb(settings["hide_small"])
    )
    await callback.answer("Setting updated")


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()
