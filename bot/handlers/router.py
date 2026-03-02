"""Main bot router implemented with template-style layout."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from bot.api_client import api_client
from bot.config import settings
from bot.keyboards import inline as kb
from bot import messages as msg
from bot.services import (
    filter_assets,
    get_balance_data,
    get_user_settings,
    is_local_user_allowed,
    parse_balances,
    register_subscriber,
)

router = Router(name="main")


def _build_access_denied(error_text: str) -> str:
    return f"{msg.ACCESS_DENIED}\n\n{error_text}"


def _extract_capabilities(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Entitlements/capabilities normalization.
    Supports future API payloads without hard dependency on exact schema.
    """
    for key in ("capabilities", "entitlements", "limits", "plan"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _refresh_allowed(capabilities: dict[str, Any]) -> bool:
    if not capabilities:
        return True
    for key in ("refresh", "can_refresh", "balances_refresh", "allow_refresh"):
        value = capabilities.get(key)
        if isinstance(value, bool):
            return value
    return True


def _allow_dex(capabilities: dict[str, Any]) -> bool:
    if not capabilities:
        return True

    for key in ("allow_dex", "dex", "can_use_dex"):
        value = capabilities.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0

    return True


def _extract_plan_label(payload: dict[str, Any]) -> str | None:
    plan = payload.get("plan")
    if isinstance(plan, dict):
        code = plan.get("code")
        name = plan.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        if isinstance(code, str) and code.strip():
            return code.strip().upper()

    code = payload.get("plan_code")
    if isinstance(code, str) and code.strip():
        return code.strip().upper()

    return None


async def _load_entitlements() -> dict[str, Any]:
    try:
        payload = await api_client.get_capabilities()
    except Exception:
        return {}

    if not isinstance(payload, dict):
        return {}

    return payload


async def _menu_context() -> tuple[bool, str | None, dict[str, Any], dict[str, Any]]:
    payload = await _load_entitlements()
    capabilities = _extract_capabilities(payload)
    allow_dex = _allow_dex(capabilities)
    plan_label = _extract_plan_label(payload)
    throttling = payload.get("throttling") if isinstance(payload.get("throttling"), dict) else {}
    return allow_dex, plan_label, capabilities, throttling


def _build_refresh_error(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status", None)
    if status == 429:
        retry_after = response.headers.get("Retry-After") if response else None
        if retry_after:
            return f"Refresh is rate-limited. Try again in {retry_after} seconds."
        return "Refresh is rate-limited. Try again later."
    return f"{msg.ERROR_PREFIX}: {exc}"


async def _ensure_access(user_id: int) -> str | None:
    """
    Primary auth: SaaS API token/org via backend API response.
    Optional fallback: local allowlist when SaaS auth is not configured.
    Returns error text if denied, otherwise None.
    """
    if not is_local_user_allowed(user_id):
        return msg.ACCESS_DENIED

    try:
        await api_client.get_balances(cached=True)
    except Exception as exc:
        text = str(exc)
        if "401" in text or "403" in text:
            return _build_access_denied("SaaS auth failed. Check API_TOKEN/API_ORG_ID.")
    return None


async def _render_main() -> tuple[str, Any]:
    allow_dex, plan_label, capabilities, throttling = await _menu_context()
    can_refresh = _refresh_allowed(capabilities)
    retry_after = int(throttling.get("retry_after_seconds") or 0)

    try:
        summary = await api_client.get_dashboard_summary()
        text = msg.format_dashboard_summary(summary)
        return (
            text,
            kb.main_menu(
                allow_dex=allow_dex,
                plan_label=plan_label,
                can_refresh=can_refresh,
                retry_after_seconds=retry_after,
            ),
        )
    except Exception:
        data = await get_balance_data()
        parsed = parse_balances(data)
        freshness = msg.format_timestamp(data)
        text = msg.format_balance_overview(parsed, freshness)
        refresh_state = msg.format_refresh_state(capabilities, throttling)
        return (
            f"{text}\n\n{refresh_state}",
            kb.main_menu(
                allow_dex=allow_dex,
                plan_label=plan_label,
                can_refresh=can_refresh,
                retry_after_seconds=retry_after,
            ),
        )


async def _edit_main(callback: CallbackQuery) -> None:
    text, keyboard = await _render_main()
    await callback.message.edit_text(text, reply_markup=keyboard)


async def _main_menu_keyboard() -> Any:
    allow_dex, plan_label, capabilities, throttling = await _menu_context()
    return kb.main_menu(
        allow_dex=allow_dex,
        plan_label=plan_label,
        can_refresh=_refresh_allowed(capabilities),
        retry_after_seconds=int(throttling.get("retry_after_seconds") or 0),
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id
    denied = await _ensure_access(user_id)
    if denied:
        await message.answer(denied)
        return

    register_subscriber(user_id)

    try:
        text, keyboard = await _render_main()
        await message.answer(text, reply_markup=keyboard)
    except Exception as exc:
        await message.answer(f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard())


@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        await callback.message.edit_text(denied, reply_markup=await _main_menu_keyboard())
        return

    try:
        await _edit_main(callback)
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )
    await callback.answer()


@router.callback_query(F.data == "refresh")
async def refresh_data(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    await callback.answer("🔄 Refreshing...")

    try:
        _, _, capabilities, throttling = await _menu_context()
        if not _refresh_allowed(capabilities):
            retry_after = int(throttling.get("retry_after_seconds") or 0)
            text = "Refresh is unavailable for your current plan."
            if retry_after > 0:
                text = f"Refresh is rate-limited. Try again in {retry_after} seconds."
            await callback.message.edit_text(
                text,
                reply_markup=await _main_menu_keyboard(),
            )
            return

        await api_client.refresh()
        await get_balance_data(force_update_cache=True)
        await _edit_main(callback)
    except Exception as exc:
        await callback.message.edit_text(
            _build_refresh_error(exc), reply_markup=await _main_menu_keyboard()
        )


@router.callback_query(F.data == "spot")
async def show_spot(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        user_settings = get_user_settings(callback.from_user.id)

        lines = msg.section_title("Spot Balances", parsed["spot_total"])
        exchanges: list[str] = []

        for name, acc in sorted(
            parsed["spot_exchanges"].items(),
            key=lambda item: item[1].get("total_usd", 0),
            reverse=True,
        ):
            total = acc.get("total_usd", 0)
            if user_settings["hide_small"] and total < settings.hide_small_balance_threshold:
                continue
            lines.append(f"{name}: {msg.format_usd(total)}")
            exchanges.append(name)

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=kb.spot_exchanges(exchanges)
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )

    await callback.answer()


@router.callback_query(F.data.startswith("spot_page_"))
async def spot_page(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    page = int(callback.data.split("_")[-1])

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        user_settings = get_user_settings(callback.from_user.id)

        lines = msg.section_title("Spot Balances", parsed["spot_total"])
        exchanges: list[str] = []

        for name, acc in sorted(
            parsed["spot_exchanges"].items(),
            key=lambda item: item[1].get("total_usd", 0),
            reverse=True,
        ):
            total = acc.get("total_usd", 0)
            if user_settings["hide_small"] and total < settings.hide_small_balance_threshold:
                continue
            lines.append(f"{name}: {msg.format_usd(total)}")
            exchanges.append(name)

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=kb.spot_exchanges(exchanges, page)
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )

    await callback.answer()


@router.callback_query(F.data.startswith("spot_") & ~F.data.startswith("spot_page_"))
async def spot_exchange_detail(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    exchange = callback.data.replace("spot_", "")

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        user_settings = get_user_settings(callback.from_user.id)

        acc = parsed["spot_exchanges"].get(exchange, {})
        assets = sorted(
            acc.get("assets", []),
            key=lambda item: item.get("value_usd", 0),
            reverse=True,
        )
        assets = filter_assets(assets, user_settings["hide_small"])

        lines = [
            f"{exchange} - Spot",
            f"{'=' * 30}",
            f"Total: {msg.format_usd(acc.get('total_usd', 0))}",
            "",
        ]

        for asset in assets[:20]:
            lines.append(
                f"{asset.get('coin', '?')}: {asset.get('amount', 0):.6g} ({msg.format_usd(asset.get('value_usd', 0))})"
            )

        if len(assets) > 20:
            lines.append(f"\n... and {len(assets) - 20} more")

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=kb.exchange_detail("spot")
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )

    await callback.answer()


@router.callback_query(F.data == "back_spot")
async def back_spot(callback: CallbackQuery) -> None:
    await show_spot(callback)


@router.callback_query(F.data == "futures")
async def show_futures(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        user_settings = get_user_settings(callback.from_user.id)

        lines = msg.section_title("Futures Balances", parsed["futures_total"])
        exchanges: list[str] = []

        for name, acc in sorted(
            parsed["futures_exchanges"].items(),
            key=lambda item: item[1].get("total_usd", 0),
            reverse=True,
        ):
            total = acc.get("total_usd", 0)
            if user_settings["hide_small"] and total < settings.hide_small_balance_threshold:
                continue
            lines.append(f"{name}: {msg.format_usd(total)}")
            exchanges.append(name)

        if not exchanges:
            lines.append("No futures balances")

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=kb.futures_exchanges(exchanges)
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )

    await callback.answer()


@router.callback_query(F.data.startswith("futures_page_"))
async def futures_page(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    page = int(callback.data.split("_")[-1])

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        user_settings = get_user_settings(callback.from_user.id)

        lines = msg.section_title("Futures Balances", parsed["futures_total"])
        exchanges: list[str] = []

        for name, acc in sorted(
            parsed["futures_exchanges"].items(),
            key=lambda item: item[1].get("total_usd", 0),
            reverse=True,
        ):
            total = acc.get("total_usd", 0)
            if user_settings["hide_small"] and total < settings.hide_small_balance_threshold:
                continue
            lines.append(f"{name}: {msg.format_usd(total)}")
            exchanges.append(name)

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=kb.futures_exchanges(exchanges, page)
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )

    await callback.answer()


@router.callback_query(
    F.data.startswith("futures_") & ~F.data.startswith("futures_page_")
)
async def futures_exchange_detail(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    exchange = callback.data.replace("futures_", "")

    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        user_settings = get_user_settings(callback.from_user.id)

        acc = parsed["futures_exchanges"].get(exchange, {})
        assets = sorted(
            acc.get("assets", []),
            key=lambda item: item.get("value_usd", 0),
            reverse=True,
        )
        assets = filter_assets(assets, user_settings["hide_small"])

        lines = [
            f"{exchange} - Futures",
            f"{'=' * 30}",
            f"Total: {msg.format_usd(acc.get('total_usd', 0))}",
            "",
        ]

        for asset in assets[:20]:
            lines.append(
                f"{asset.get('coin', '?')}: {asset.get('amount', 0):.6g} ({msg.format_usd(asset.get('value_usd', 0))})"
            )

        if len(assets) > 20:
            lines.append(f"\n... and {len(assets) - 20} more")

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=kb.exchange_detail("futures")
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )

    await callback.answer()


@router.callback_query(F.data == "back_futures")
async def back_futures(callback: CallbackQuery) -> None:
    await show_futures(callback)


@router.callback_query(F.data == "dex")
async def show_dex(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    _, _, capabilities, _ = await _menu_context()
    if not _allow_dex(capabilities):
        await callback.answer("DEX is unavailable on your plan")
        await callback.message.edit_text(
            "DEX is unavailable on your current plan.",
            reply_markup=await _main_menu_keyboard(),
        )
        return

    await _show_dex_page(callback, 0)


@router.callback_query(F.data.startswith("dex_page_"))
async def dex_page_handler(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    _, _, capabilities, _ = await _menu_context()
    if not _allow_dex(capabilities):
        await callback.answer("DEX is unavailable on your plan")
        await callback.message.edit_text(
            "DEX is unavailable on your current plan.",
            reply_markup=await _main_menu_keyboard(),
        )
        return

    page = int(callback.data.split("_")[-1])
    await _show_dex_page(callback, page)


async def _show_dex_page(callback: CallbackQuery, page: int) -> None:
    try:
        data = await get_balance_data()
        parsed = parse_balances(data)
        user_settings = get_user_settings(callback.from_user.id)

        all_assets: list[dict[str, Any]] = []
        for acc in parsed["dex_wallets"].values():
            all_assets.extend(acc.get("assets", []))

        all_assets = sorted(
            all_assets,
            key=lambda item: item.get("value_usd", 0),
            reverse=True,
        )
        all_assets = filter_assets(all_assets, user_settings["hide_small"])

        per_page = 20
        total_pages = max(1, math.ceil(len(all_assets) / per_page))
        start = page * per_page
        end = start + per_page
        page_assets = all_assets[start:end]

        lines = msg.section_title("DEX Wallet", parsed["dex_total"])
        for asset in page_assets:
            lines.append(
                f"{asset.get('coin', '?')}: {asset.get('amount', 0):.6g} ({msg.format_usd(asset.get('value_usd', 0))})"
            )

        if not page_assets:
            lines.append("No assets")

        await callback.message.edit_text(
            "\n".join(lines), reply_markup=kb.dex(page, total_pages)
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )

    await callback.answer()


@router.callback_query(F.data == "transactions")
async def show_transactions(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    await _show_transactions_page(callback, 0)


@router.callback_query(F.data.startswith("transactions_page_"))
async def transactions_page(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    page = int(callback.data.rsplit("_", 1)[-1])
    await _show_transactions_page(callback, page)


@router.callback_query(F.data == "transactions_refresh")
async def transactions_refresh(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    await callback.answer("Refreshing transactions...")

    try:
        user_settings = get_user_settings(callback.from_user.id)
        since_hours = int(user_settings.get("tx_since_hours", 24) or 24)
        await api_client.refresh_transactions(since_hours=since_hours)
        await _show_transactions_page(callback, 0, answer_query=False)
    except Exception as exc:
        await callback.message.edit_text(
            _build_refresh_error(exc), reply_markup=await _main_menu_keyboard()
        )


@router.callback_query(F.data == "transactions_filter_type")
async def transactions_filter_type(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    user_settings = get_user_settings(callback.from_user.id)
    current = user_settings.get("tx_type", "all")
    order = ["all", "deposit", "withdrawal"]
    next_value = order[(order.index(current) + 1) % len(order)] if current in order else "all"
    user_settings["tx_type"] = next_value
    await _show_transactions_page(callback, 0, answer_query=False)


@router.callback_query(F.data == "transactions_filter_status")
async def transactions_filter_status(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    user_settings = get_user_settings(callback.from_user.id)
    current = user_settings.get("tx_status", "all")
    order = ["all", "pending", "ok", "failed"]
    next_value = order[(order.index(current) + 1) % len(order)] if current in order else "all"
    user_settings["tx_status"] = next_value
    await _show_transactions_page(callback, 0, answer_query=False)


@router.callback_query(F.data == "transactions_filter_since")
async def transactions_filter_since(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    user_settings = get_user_settings(callback.from_user.id)
    current = int(user_settings.get("tx_since_hours", 24) or 24)
    order = [24, 72, 168]
    next_value = order[(order.index(current) + 1) % len(order)] if current in order else 24
    user_settings["tx_since_hours"] = next_value
    await _show_transactions_page(callback, 0, answer_query=False)


@router.callback_query(F.data == "transactions_filter_reset")
async def transactions_filter_reset(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    user_settings = get_user_settings(callback.from_user.id)
    user_settings["tx_type"] = "all"
    user_settings["tx_status"] = "all"
    user_settings["tx_service"] = "all"
    user_settings["tx_since_hours"] = 24
    await _show_transactions_page(callback, 0, answer_query=False)


@router.callback_query(F.data == "integrations")
async def show_integrations(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    try:
        integrations = await api_client.get_integrations(include_inactive=True)
        if not isinstance(integrations, list):
            integrations = []
        lines = ["Integrations", f"{'=' * 30}", ""]
        if not integrations:
            lines.append("No integrations")
        else:
            for item in integrations:
                name = item.get("name", f"integration-{item.get('id', '?')}")
                state = "active" if item.get("is_active") else "inactive"
                kind = item.get("kind", "unknown")
                lines.append(f"• {name} ({kind}) — {state}")

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=kb.integrations(integrations),
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )

    await callback.answer()


@router.callback_query(F.data.startswith("integration_select_"))
async def select_integration(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    integration_id = int(callback.data.rsplit("_", 1)[-1])

    try:
        await _render_integration_detail(callback, integration_id)
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )

    await callback.answer()


@router.callback_query(F.data.startswith("integration_deactivate_"))
async def deactivate_integration(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    integration_id = int(callback.data.rsplit("_", 1)[-1])

    try:
        await api_client.deactivate_integration(integration_id)
        await show_integrations(callback)
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )
        await callback.answer()


@router.callback_query(F.data.startswith("integration_activate_"))
async def activate_integration(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    integration_id = int(callback.data.rsplit("_", 1)[-1])

    try:
        await api_client.activate_integration(integration_id)
        await show_integrations(callback)
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )
        await callback.answer()


@router.callback_query(F.data.startswith("integration_refresh_"))
async def refresh_integration(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    integration_id = int(callback.data.rsplit("_", 1)[-1])

    try:
        result = await api_client.refresh_integration(integration_id)
        job_id = result.get("job_id")
        await callback.answer(f"Refresh queued (job {job_id})")
        await _render_integration_detail(callback, integration_id, job_id=job_id)
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )
        await callback.answer()


async def _render_integration_detail(
    callback: CallbackQuery,
    integration_id: int,
    job_id: int | None = None,
) -> None:
    integrations = await api_client.get_integrations(include_inactive=True)
    if not isinstance(integrations, list):
        integrations = []
    item = next((i for i in integrations if int(i.get("id", -1)) == integration_id), None)
    if item is None:
        await callback.message.edit_text(
            "Integration not found",
            reply_markup=await _main_menu_keyboard(),
        )
        return

    name = item.get("name", f"integration-{integration_id}")
    kind = item.get("kind", "unknown")
    provider = item.get("provider", "unknown")
    is_active = bool(item.get("is_active"))
    status_text = "active" if is_active else "inactive"

    lines = [
        "Integration",
        f"{'=' * 30}",
        "",
        f"Name: {name}",
        f"Provider: {provider}",
        f"Kind: {kind}",
        f"Status: {status_text}",
    ]
    if job_id is not None:
        lines.append(f"Refresh job: {job_id}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb.integration_actions(integration_id, is_active=is_active),
    )


async def _show_transactions_page(
    callback: CallbackQuery,
    page: int,
    answer_query: bool = True,
) -> None:
    limit = 10
    offset = page * limit

    try:
        user_settings = get_user_settings(callback.from_user.id)
        tx_type = user_settings.get("tx_type", "all")
        tx_status = user_settings.get("tx_status", "all")
        tx_service = user_settings.get("tx_service", "all")
        tx_since_hours = int(user_settings.get("tx_since_hours", 24) or 24)

        now = datetime.now(timezone.utc)
        start_date = now - timedelta(hours=tx_since_hours)

        response = await api_client.get_transactions(
            limit=limit,
            offset=offset,
            tx_type=None if tx_type == "all" else tx_type,
            status=None if tx_status == "all" else tx_status,
            service=None if tx_service == "all" else tx_service,
            start_date=start_date,
        )
        rows = response.get("transactions", [])

        lines = [
            "Transactions",
            f"{'=' * 30}",
            f"Filters: type={tx_type}, status={tx_status}, window={tx_since_hours}h",
            "",
        ]
        if not rows:
            lines.append("No transactions")
        else:
            for tx in rows:
                row_type = tx.get("tx_type", "unknown")
                direction = "📥" if row_type == "deposit" else "📤"
                amount = float(tx.get("amount", 0))
                currency = tx.get("currency", "?")
                service = tx.get("service", "unknown")
                status = tx.get("status", "pending")
                lines.append(
                    f"{direction} {service.upper()} • {amount:.6g} {currency} • {status}"
                )

        has_prev = page > 0
        has_next = len(rows) == limit

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=kb.transactions(
                page=page,
                has_prev=has_prev,
                has_next=has_next,
                selected_type=tx_type,
                selected_status=tx_status,
                selected_since_hours=tx_since_hours,
            ),
        )
    except Exception as exc:
        await callback.message.edit_text(
            f"{msg.ERROR_PREFIX}: {exc}", reply_markup=await _main_menu_keyboard()
        )

    if answer_query:
        await callback.answer()


@router.callback_query(F.data == "settings")
async def show_settings(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    user_settings = get_user_settings(callback.from_user.id)
    _, plan_label, _, _ = await _menu_context()
    plan_line = f"\nPlan: {plan_label}" if plan_label else ""
    text = (
        "Settings\n"
        f"{'=' * 30}\n\n"
        f"Hide balances less than ${settings.hide_small_balance_threshold:.2f}"
        f"{plan_line}"
    )

    await callback.message.edit_text(text, reply_markup=kb.settings(user_settings["hide_small"]))
    await callback.answer()


@router.callback_query(F.data == "toggle_hide_small")
async def toggle_hide_small(callback: CallbackQuery) -> None:
    denied = await _ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        return

    user_settings = get_user_settings(callback.from_user.id)
    user_settings["hide_small"] = not user_settings["hide_small"]

    _, plan_label, _, _ = await _menu_context()
    plan_line = f"\nPlan: {plan_label}" if plan_label else ""
    text = (
        "Settings\n"
        f"{'=' * 30}\n\n"
        f"Hide balances less than ${settings.hide_small_balance_threshold:.2f}"
        f"{plan_line}"
    )

    await callback.message.edit_text(text, reply_markup=kb.settings(user_settings["hide_small"]))
    await callback.answer("Setting updated")


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
