"""Main bot router with route-aware single-message navigation."""

from __future__ import annotations

import logging
import time
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import messages as msg
from bot.contracts.callbacks import (
    ACTION_BACK,
    ACTION_CYCLE,
    ACTION_INPUT_START,
    ACTION_INTEGRATION_ACTIVATE,
    ACTION_INTEGRATION_DEACTIVATE,
    ACTION_INTEGRATION_DELETE,
    ACTION_INTEGRATION_REFRESH,
    ACTION_NOOP,
    ACTION_OPEN,
    ACTION_PAGE,
    ACTION_REFRESH,
    ACTION_RESET,
    ACTION_SELECT,
    ACTION_TOGGLE,
    ROUTE_DEX,
    ROUTE_FUTURES_DETAIL,
    ROUTE_FUTURES_LIST,
    ROUTE_INTEGRATION_DETAIL,
    ROUTE_INTEGRATION_EXCHANGE_PICKER,
    ROUTE_INTEGRATION_WALLET_PICKER,
    ROUTE_INTEGRATIONS,
    ROUTE_INPUT,
    ROUTE_MAIN,
    ROUTE_NOTIFICATIONS,
    ROUTE_PAYMENTS,
    ROUTE_PLAN,
    ROUTE_ADMIN,
    ROUTE_ADMIN_USERS,
    ROUTE_ADMIN_USER_DETAIL,
    ROUTE_SETTINGS,
    ROUTE_SPOT_DETAIL,
    ROUTE_SPOT_LIST,
    ROUTE_TRANSACTIONS,
    decode_input_kind,
    parse_callback,
    parse_payload_int,
    parse_payload_jsonish,
)
from bot.repositories.user_repository import UserRepository
from bot.services.navigation_service import NavigationService
from bot.services.screen_service import RenderedScreen, ScreenService
from bot.state.repository import UiStateRepository
from bot.states.input import UiInputStates

router = Router(name="main")
logger = logging.getLogger(__name__)


def _access_denied_reason_text(reason: str) -> str:
    if not reason or reason == msg.ACCESS_DENIED:
        return "You do not have access to this bot."
    return reason


def _exception_status(exc: Exception) -> int | None:
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status
    return None


def _exception_retry_after(exc: Exception) -> int:
    headers = getattr(exc, "headers", None)
    if not headers:
        return 0
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return 0
    try:
        return max(0, int(float(raw or 0)))
    except (TypeError, ValueError):
        return 0


def _friendly_error_screen(exc: Exception, *, route: str, payload: dict[str, Any], keyboard: Any) -> RenderedScreen:
    if _exception_status(exc) == 429:
        return RenderedScreen(
            route=route,
            payload=payload,
            text=msg.refresh_unavailable_text(_exception_retry_after(exc)),
            keyboard=keyboard,
        )
    return RenderedScreen(
        route=route,
        payload=payload,
        text=msg.error_text(exc),
        keyboard=keyboard,
    )


async def _edit_message(message: Message, rendered: RenderedScreen) -> None:
    kwargs = msg.as_edit_kwargs(rendered.text, rendered.keyboard)
    try:
        await message.edit_text(**kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        raise


async def _answer_message(message: Message, rendered: RenderedScreen) -> Message:
    kwargs = rendered.text.as_kwargs()
    kwargs["reply_markup"] = rendered.keyboard
    return await message.answer(**kwargs)


async def _render_and_edit(
    callback: CallbackQuery,
    *,
    state: FSMContext,
    screen_service: ScreenService,
    nav_service: NavigationService,
    route: str,
    payload: dict[str, Any],
    source_route: str | None,
    push_current: bool,
) -> None:
    t0 = time.monotonic()
    # Answer callback IMMEDIATELY so user sees the loading indicator
    await callback.answer()
    t_answer = time.monotonic()

    if push_current:
        ui_state = await nav_service.forward(
            state,
            route=route,
            payload=payload,
            source_route=source_route,
        )
    else:
        ui_state = await nav_service.replace_current(
            state,
            route=route,
            payload=payload,
            source_route=source_route,
        )
    t_nav = time.monotonic()

    rendered = await screen_service.render(
        route=ui_state.current_route,
        payload=ui_state.payload,
        user_id=callback.from_user.id,
        rev=ui_state.revision,
    )
    t_render = time.monotonic()
    await _edit_message(callback.message, rendered)
    t_edit = time.monotonic()
    logger.info(
        "callback_timing route=%s answer_ms=%d nav_ms=%d render_ms=%d edit_ms=%d total_ms=%d",
        ui_state.current_route,
        int((t_answer - t0) * 1000),
        int((t_nav - t_answer) * 1000),
        int((t_render - t_nav) * 1000),
        int((t_edit - t_render) * 1000),
        int((t_edit - t0) * 1000),
    )


async def _recover_current(
    callback: CallbackQuery,
    *,
    state: FSMContext,
    screen_service: ScreenService,
    nav_service: NavigationService,
) -> None:
    ui_state = await nav_service.current(state)
    if not ui_state.current_route:
        ui_state = await nav_service.recover(state)

    rendered = await screen_service.render(
        route=ui_state.current_route,
        payload=ui_state.payload,
        user_id=callback.from_user.id,
        rev=ui_state.revision,
    )
    await _edit_message(callback.message, rendered)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    user_repo: UserRepository,
    ui_state_repo: UiStateRepository,
    nav_service: NavigationService,
    screen_service: ScreenService,
) -> None:
    user_id = message.from_user.id
    logger.info("cmd_start user_id=%s", user_id)

    denied = await screen_service.ensure_access(user_id)
    if denied:
        ui_state = await ui_state_repo.load(state)
        if ui_state.anchor_message_id is not None:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=ui_state.anchor_message_id,
                    **msg.access_denied_text(
                        _access_denied_reason_text(denied)
                    ).as_kwargs(),
                )
                return
            except Exception:
                pass

        await message.answer(
            **msg.access_denied_text(_access_denied_reason_text(denied)).as_kwargs()
        )
        return

    await user_repo.register_subscriber(user_id)
    ui_state = await nav_service.replace_current(
        state,
        route=ROUTE_MAIN,
        payload={},
        source_route=None,
    )

    # Reply immediately so /start never appears frozen while dashboard loads.
    sent = await message.answer(**msg.loading_text("Loading dashboard...").as_kwargs())
    await ui_state_repo.save_anchor(state, sent.message_id)

    try:
        import asyncio
        rendered = await asyncio.wait_for(
            screen_service.render(
                route=ROUTE_MAIN, payload={}, user_id=user_id, rev=ui_state.revision
            ),
            timeout=20,
        )
        await _edit_message(sent, rendered)
    except Exception as exc:
        logger.warning("cmd_start_render_failed user_id=%s error=%s", user_id, exc)
        fallback = RenderedScreen(
            route=ROUTE_MAIN,
            payload={},
            text=msg.dashboard_text(
                {
                    "total_usd": 0.0,
                    "exchanges_count": 0,
                    "spot_total": 0.0,
                    "futures_total": 0.0,
                    "dex_total": 0.0,
                    "plan": {"name": "-", "code": "unknown"},
                    "capabilities": {"can_refresh": True},
                    "throttling": {},
                    "integrations": {"total": 0, "active": 0},
                    "transactions_24h": {"total": 0, "pending": 0, "failed": 0},
                    "freshness": "temporarily unavailable",
                }
            ),
            keyboard=await screen_service.render(
                route=ROUTE_MAIN, payload={}, user_id=user_id, rev=ui_state.revision
            ).keyboard if False else None,
        )
        # If fallback rendering fails, leave loading message instead of crashing.
        try:
            rendered = await screen_service.render(
                route=ROUTE_MAIN, payload={}, user_id=user_id, rev=ui_state.revision
            )
            await _edit_message(sent, rendered)
        except Exception:
            pass


@router.callback_query()
async def handle_callback(
    callback: CallbackQuery,
    state: FSMContext,
    ui_state_repo: UiStateRepository,
    nav_service: NavigationService,
    screen_service: ScreenService,
) -> None:
    denied = await screen_service.ensure_access(callback.from_user.id)
    if denied:
        await callback.answer(msg.ACCESS_DENIED)
        await _edit_message(
            callback.message,
            RenderedScreen(
                route=ROUTE_MAIN,
                payload={},
                text=msg.access_denied_text(_access_denied_reason_text(denied)),
                keyboard=(
                    await screen_service.render(
                        route=ROUTE_MAIN,
                        payload={},
                        user_id=callback.from_user.id,
                        rev=0,
                    )
                ).keyboard,
            ),
        )
        return

    command = parse_callback(callback.data)
    if command is None:
        await callback.answer()
        await _recover_current(
            callback,
            state=state,
            screen_service=screen_service,
            nav_service=nav_service,
        )
        return

    payload = parse_payload_jsonish(command.payload)
    if (
        command.source == "broadcast"
        and command.action == ACTION_OPEN
        and command.route in (ROUTE_INTEGRATIONS, ROUTE_PLAN)
    ):
        await ui_state_repo.save_anchor(state, callback.message.message_id)
        await _render_and_edit(
            callback,
            state=state,
            screen_service=screen_service,
            nav_service=nav_service,
            route=command.route,
            payload=payload,
            source_route=ROUTE_MAIN,
            push_current=False,
        )
        return

    ui_state = await ui_state_repo.ensure_anchor(state, callback.message.message_id)
    if (
        ui_state.anchor_message_id
        and callback.message.message_id != ui_state.anchor_message_id
    ):
        await callback.answer()
        await _recover_current(
            callback,
            state=state,
            screen_service=screen_service,
            nav_service=nav_service,
        )
        return

    if command.rev != ui_state.revision:
        await callback.answer()
        await _recover_current(
            callback,
            state=state,
            screen_service=screen_service,
            nav_service=nav_service,
        )
        return

    # Answer callback IMMEDIATELY for all navigation actions to reduce perceived latency
    # (user sees instant response, message edits come shortly after)
    if command.action not in (ACTION_REFRESH,) and command.route != ROUTE_PLAN:
        await callback.answer()

    try:
        if command.action == ACTION_NOOP:
            return

        if command.action == ACTION_BACK:
            if ui_state.waiting_input:
                await nav_service.set_waiting_input(state, None)
                await state.set_state(None)
            next_state = await nav_service.back(state)
            rendered = await screen_service.render(
                route=next_state.current_route,
                payload=next_state.payload,
                user_id=callback.from_user.id,
                rev=next_state.revision,
            )
            await _edit_message(callback.message, rendered)
            return

        if command.route == ROUTE_MAIN and command.action == ACTION_REFRESH:
            await callback.answer("Refreshing...")
            await screen_service.refresh_main()
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_MAIN,
                payload={},
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if (
            command.route == ROUTE_MAIN
            and command.action == ACTION_SELECT
            and str(payload.get("field") or "") == "notifications"
        ):
            await callback.answer(
                "\U0001f6a7 Under development",
                show_alert=True,
            )
            return


        if command.route == ROUTE_PLAN and command.action == ACTION_REFRESH:
            await callback.answer("Refreshing...")
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_PLAN,
                payload=ui_state.payload,
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if command.route == ROUTE_PLAN and command.action == ACTION_SELECT:
            plan_code = str(payload.get("plan_code") or "").strip().lower()
            if not plan_code:
                await callback.answer()
                await _recover_current(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                )
                return

            plans_payload = await screen_service.api_repo.get_billing_plans()
            plans = (
                plans_payload.get("plans")
                if isinstance(plans_payload.get("plans"), list)
                else []
            )
            selected_plan = next(
                (
                    item
                    for item in plans
                    if isinstance(item, dict)
                    and str(item.get("code") or "").strip().lower() == plan_code
                ),
                None,
            )
            current_payload = await screen_service.api_repo.get_billing_current()
            current_plan = (
                current_payload.get("plan")
                if isinstance(current_payload.get("plan"), dict)
                else {}
            )
            current_plan_code = str(current_plan.get("code") or "free").strip().lower()
            if current_plan_code == "full":
                current_plan_code = "pro"

            if selected_plan is None:
                await callback.answer("Plan not found", show_alert=False)
                await _recover_current(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                )
                return

            if plan_code == current_plan_code:
                await callback.answer()
                return

            price = float(selected_plan.get("price_monthly") or 0.0)
            current_plan_payload = next(
                (
                    item
                    for item in plans
                    if isinstance(item, dict)
                    and str(item.get("code") or "").strip().lower() == current_plan_code
                ),
                {},
            )
            current_price = float(current_plan_payload.get("price_monthly") or 0.0)

            if price <= current_price:
                try:
                    await screen_service.switch_plan(plan_code)
                except Exception as exc:
                    logger.warning("Failed to switch plan to %s: %s", plan_code, exc)
                    await callback.answer(
                        "Не удалось переключить тариф. Попробуйте позже.",
                        show_alert=True,
                    )
                    return
                await callback.answer("Тариф переключён")
                await _render_and_edit(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                    route=ROUTE_PLAN,
                    payload={},
                    source_route=ui_state.source_route,
                    push_current=False,
                )
                return

            try:
                created = await screen_service.create_plan_invoice(plan_code, price)
            except Exception as exc:
                logger.warning("Failed to create plan invoice for %s: %s", plan_code, exc)
                await callback.answer(
                    "Оплата временно недоступна. Попробуйте позже или напишите администратору.",
                    show_alert=True,
                )
                return

            invoice_id = (
                parse_payload_int(created.get("id"), default=-1)
                if isinstance(created, dict)
                else -1
            )
            next_route = ROUTE_PAYMENTS if invoice_id > 0 else ROUTE_PLAN
            next_payload = {"id": invoice_id} if invoice_id > 0 else {}
            await callback.answer("Invoice created")
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=next_route,
                payload=next_payload,
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if command.route == ROUTE_PAYMENTS and command.action == ACTION_REFRESH:
            invoice_id = parse_payload_int(payload.get("id"), default=-1)
            if invoice_id > 0:
                await screen_service.refresh_payment_invoice(invoice_id)
            await callback.answer("Refreshing...")
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_PAYMENTS,
                payload={"id": invoice_id} if invoice_id > 0 else {},
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if command.route == ROUTE_PAYMENTS and command.action == ACTION_SELECT:
            amount = parse_payload_int(payload.get("amount"), default=0)
            if amount <= 0:
                await callback.answer()
                await _recover_current(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                )
                return
            created = await screen_service.create_topup_invoice(float(amount))
            invoice_id = (
                parse_payload_int(created.get("id"), default=-1)
                if isinstance(created, dict)
                else -1
            )
            await callback.answer("Invoice created")
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_PAYMENTS,
                payload={"id": invoice_id} if invoice_id > 0 else {},
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if (
            command.route in {ROUTE_SPOT_LIST, ROUTE_FUTURES_LIST, ROUTE_DEX}
            and command.action == ACTION_REFRESH
        ):
            await callback.answer("Refreshing...")
            await screen_service.refresh_main()
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=command.route,
                payload=ui_state.payload,
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if (
            command.route in {ROUTE_SPOT_DETAIL, ROUTE_FUTURES_DETAIL}
            and command.action == ACTION_REFRESH
        ):
            await callback.answer("Refreshing...")
            await screen_service.refresh_main()
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=command.route,
                payload=ui_state.payload,
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if command.route == ROUTE_TRANSACTIONS and command.action == ACTION_REFRESH:
            try:
                await callback.answer("Refreshing transactions...")
                await screen_service.refresh_transactions(user_id=callback.from_user.id)
            except Exception as exc:
                text = str(exc)
                if "429" in text:
                    await callback.answer(
                        "Transactions refresh is rate-limited.", show_alert=False
                    )
                else:
                    await callback.answer(
                        "Transactions refresh failed.", show_alert=False
                    )
                await _recover_current(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                )
                return
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_TRANSACTIONS,
                payload={**ui_state.payload, "page": 0},
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if (
            command.route == ROUTE_TRANSACTIONS
            and command.action == ACTION_SELECT
            and "field" in payload
        ):
            field = str(payload.get("field") or "")
            value = str(payload.get("value") or "")
            await screen_service.apply_transactions_filter(
                user_id=callback.from_user.id,
                field=field,
                value=value,
            )
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_TRANSACTIONS,
                payload={"page": 0},
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if command.route == ROUTE_TRANSACTIONS and command.action == ACTION_RESET:
            await screen_service.reset_transactions_filters(
                user_id=callback.from_user.id
            )
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_TRANSACTIONS,
                payload={"page": 0},
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if command.route == ROUTE_NOTIFICATIONS and command.action == ACTION_TOGGLE:
            field = str(payload.get("field") or "")
            if field in {"enabled", "system_enabled", "transaction_enabled", "balance_enabled"}:
                await screen_service.update_notification_toggle(field=field)
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=command.route,
                payload=ui_state.payload,
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if command.route == ROUTE_SETTINGS and command.action == ACTION_TOGGLE:
            await screen_service.update_hide_small(user_id=callback.from_user.id)
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=command.route,
                payload=ui_state.payload,
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if command.route == ROUTE_SETTINGS and command.action == ACTION_SELECT:
            field = str(payload.get("field") or "")
            if field == "currency":
                await screen_service.cycle_display_currency(user_id=callback.from_user.id)
            else:
                await screen_service.apply_transactions_filter(
                    user_id=callback.from_user.id,
                    field=field,
                    value=str(payload.get("value") or ""),
                )
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=command.route,
                payload=ui_state.payload,
                source_route=ui_state.source_route,
                push_current=False,
            )
            return


        if (
            command.route == ROUTE_ADMIN_USER_DETAIL
            and command.action == ACTION_REFRESH
        ):
            await callback.answer("Refreshing...")
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_ADMIN_USER_DETAIL,
                payload={
                    "user_id": parse_payload_int(payload.get("user_id"), default=0),
                    "organization_id": parse_payload_int(
                        payload.get("organization_id"), default=0
                    ),
                },
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if command.route == ROUTE_INTEGRATIONS and command.action == ACTION_REFRESH:
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_INTEGRATIONS,
                payload={},
                source_route=ui_state.source_route,
                push_current=False,
            )
            return

        if (
            command.route == ROUTE_INTEGRATION_EXCHANGE_PICKER
            and command.action == ACTION_OPEN
        ):
            allowed, reason = await screen_service.can_add_cex_account(
                user_id=callback.from_user.id
            )
            if not allowed:
                await _render_and_edit(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                    route=ROUTE_PLAN,
                    payload={"reason": reason or "cex_limit_reached"},
                    source_route=ui_state.current_route,
                    push_current=True,
                )
                await callback.answer()
                return
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=command.route,
                payload=payload,
                source_route=command.source or ui_state.current_route,
                push_current=True,
            )
            await callback.answer()
            return

        if command.route == ROUTE_INTEGRATION_DETAIL and command.action in {
            ACTION_INTEGRATION_ACTIVATE,
            ACTION_INTEGRATION_DEACTIVATE,
            ACTION_INTEGRATION_DELETE,
            ACTION_INTEGRATION_REFRESH,
        }:
            integration_id = parse_payload_int(payload.get("id"), default=-1)
            if command.action == ACTION_INTEGRATION_ACTIVATE:
                await screen_service.activate_integration(integration_id)
                await _render_and_edit(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                    route=ROUTE_INTEGRATION_DETAIL,
                    payload={"id": integration_id},
                    source_route=ROUTE_INTEGRATIONS,
                    push_current=False,
                )
            elif command.action == ACTION_INTEGRATION_DEACTIVATE:
                await screen_service.deactivate_integration(integration_id)
                await _render_and_edit(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                    route=ROUTE_INTEGRATION_DETAIL,
                    payload={"id": integration_id},
                    source_route=ROUTE_INTEGRATIONS,
                    push_current=False,
                )
            elif command.action == ACTION_INTEGRATION_DELETE:
                await screen_service.delete_integration(integration_id)
                next_state = await nav_service.back(state)
                rendered = await screen_service.render(
                    route=next_state.current_route,
                    payload=next_state.payload,
                    user_id=callback.from_user.id,
                    rev=next_state.revision,
                )
                await _edit_message(callback.message, rendered)
            else:
                job_id = await screen_service.refresh_integration(integration_id)
                detail_payload = {"id": integration_id}
                if job_id is not None:
                    detail_payload["job_id"] = job_id
                await _render_and_edit(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                    route=ROUTE_INTEGRATION_DETAIL,
                    payload=detail_payload,
                    source_route=ROUTE_INTEGRATIONS,
                    push_current=False,
                )
            await callback.answer()
            return

        if command.route == ROUTE_INPUT and command.action == ACTION_INPUT_START:
            kind = decode_input_kind(str(payload.get("kind") or ""))
            if not kind:
                await callback.answer()
                await _recover_current(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                )
                return

            waiting = screen_service.start_input_flow(
                kind=kind,
                return_route=ui_state.current_route,
                return_payload=ui_state.payload,
            )
            await nav_service.set_waiting_input(state, waiting)
            await state.set_state(UiInputStates.waiting_value)
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_INPUT,
                payload={"kind": kind},
                source_route=ui_state.current_route,
                push_current=True,
            )
            await callback.answer()
            return

        if (
            command.route == ROUTE_INTEGRATION_EXCHANGE_PICKER
            and command.action == ACTION_SELECT
        ):
            exchange_code = str(payload.get("exchange_code") or "").strip().lower()
            if not exchange_code:
                await callback.answer()
                await _recover_current(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                )
                return

            waiting = screen_service.start_exchange_account_ref_flow(
                exchange_code=exchange_code,
                return_route=ROUTE_INTEGRATIONS,
                return_payload={},
            )
            await nav_service.set_waiting_input(state, waiting)
            await state.set_state(UiInputStates.waiting_value)
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_INPUT,
                payload={"kind": "integration_cex_account_ref"},
                source_route=ui_state.current_route,
                push_current=True,
            )
            await callback.answer()
            return

        if (
            command.route == ROUTE_INTEGRATION_WALLET_PICKER
            and command.action == ACTION_OPEN
        ):
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=command.route,
                payload=payload,
                source_route=command.source or ui_state.current_route,
                push_current=True,
            )
            await callback.answer()
            return

        if (
            command.route == ROUTE_INTEGRATION_WALLET_PICKER
            and command.action == ACTION_SELECT
        ):
            wallet_provider = str(payload.get("wallet_provider") or "").strip().lower()
            if not wallet_provider:
                await callback.answer()
                await _recover_current(
                    callback,
                    state=state,
                    screen_service=screen_service,
                    nav_service=nav_service,
                )
                return

            waiting = screen_service.start_wallet_address_flow(
                wallet_provider=wallet_provider,
                return_route=ROUTE_INTEGRATIONS,
                return_payload={},
            )
            await nav_service.set_waiting_input(state, waiting)
            await state.set_state(UiInputStates.waiting_value)
            await _render_and_edit(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
                route=ROUTE_INPUT,
                payload={"kind": "integration_dex_wallet_address"},
                source_route=ui_state.current_route,
                push_current=True,
            )
            await callback.answer()
            return

        target_payload: dict[str, Any] = {}
        if command.action == ACTION_PAGE:
            target_payload = dict(ui_state.payload or {})
            target_payload["page"] = parse_payload_int(payload.get("page"), default=0)
        elif command.action == ACTION_SELECT:
            if "exchange" in payload:
                target_payload = {"exchange": str(payload.get("exchange") or "")}
            elif "i" in payload:
                target_payload = {
                    "i": parse_payload_int(payload.get("i"), default=-1),
                    "page": parse_payload_int(payload.get("page"), default=0),
                }
            elif "service" in payload:
                target_payload = {
                    "service": str(payload.get("service") or ""),
                    "page": parse_payload_int(payload.get("page"), default=0),
                }
            elif "id" in payload:
                target_payload = {
                    "id": parse_payload_int(payload.get("id"), default=-1)
                }
            elif "user_id" in payload:
                target_payload = {
                    "user_id": parse_payload_int(payload.get("user_id"), default=0),
                    "organization_id": parse_payload_int(
                        payload.get("organization_id"), default=0
                    ),
                }
        else:
            target_payload = payload

        push_current = command.action in {ACTION_OPEN, ACTION_SELECT}
        if command.action == ACTION_PAGE:
            push_current = False

        await _render_and_edit(
            callback,
            state=state,
            screen_service=screen_service,
            nav_service=nav_service,
            route=command.route,
            payload=target_payload,
            source_route=command.source or ui_state.current_route,
            push_current=push_current,
        )
        await callback.answer()
    except Exception as exc:
        await callback.answer()
        try:
            rendered = await screen_service.render(
                route=ui_state.current_route,
                payload=ui_state.payload,
                user_id=callback.from_user.id,
                rev=ui_state.revision,
            )
            await _edit_message(
                callback.message,
                _friendly_error_screen(
                    exc,
                    route=ui_state.current_route,
                    payload=ui_state.payload,
                    keyboard=rendered.keyboard,
                ),
            )
        except Exception:
            await _recover_current(
                callback,
                state=state,
                screen_service=screen_service,
                nav_service=nav_service,
            )


@router.message(UiInputStates.waiting_value)
async def handle_input_value(
    message: Message,
    state: FSMContext,
    nav_service: NavigationService,
    screen_service: ScreenService,
) -> None:
    ui_state = await nav_service.current(state)
    waiting = ui_state.waiting_input or {}
    kind = str(waiting.get("kind") or "")
    return_route = str(waiting.get("return_route") or ROUTE_MAIN)
    return_payload = (
        waiting.get("return_payload")
        if isinstance(waiting.get("return_payload"), dict)
        else {}
    )

    if not kind:
        await nav_service.set_waiting_input(state, None)
        await state.set_state(None)
        ui_state = await nav_service.back(state)
        rendered = await screen_service.render(
            route=ui_state.current_route,
            payload=ui_state.payload,
            user_id=message.from_user.id,
            rev=ui_state.revision,
        )
        if ui_state.anchor_message_id is not None:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=ui_state.anchor_message_id,
                **msg.as_edit_kwargs(rendered.text, rendered.keyboard),
            )
        return

    if kind == "integration_dex_chain":
        allowed, reason = await screen_service.can_add_evm_wallet(
            user_id=message.from_user.id,
            chain=message.text or "",
        )
        if not allowed:
            try:
                await message.delete()
            except Exception:
                pass
            await nav_service.set_waiting_input(state, None)
            await state.set_state(None)
            ui_state = await nav_service.replace_current(
                state,
                route=ROUTE_PLAN,
                payload={"reason": reason or "evm_limit_reached"},
                source_route=ui_state.source_route,
            )
            rendered = await screen_service.render(
                route=ROUTE_PLAN,
                payload={"reason": reason or "evm_limit_reached"},
                user_id=message.from_user.id,
                rev=ui_state.revision,
            )
            if ui_state.anchor_message_id is not None:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=ui_state.anchor_message_id,
                    **msg.as_edit_kwargs(rendered.text, rendered.keyboard),
                )
            return

    result = await screen_service.process_input_value(
        user_id=message.from_user.id,
        waiting=waiting,
        raw_value=message.text or "",
    )

    try:
        await message.delete()
    except Exception:
        pass

    if result.success:
        await nav_service.set_waiting_input(state, None)
        if result.next_waiting:
            ui_state = await nav_service.set_waiting_input(state, result.next_waiting)
            await state.set_state(UiInputStates.waiting_value)
            next_kind = str(result.next_waiting.get("kind") or kind)
            ui_state = await nav_service.replace_current(
                state,
                route=ROUTE_INPUT,
                payload={"kind": next_kind},
                source_route=ui_state.source_route,
            )
            rendered = await screen_service.render_input_waiting(
                kind=next_kind,
                user_id=message.from_user.id,
                rev=ui_state.revision,
            )
        else:
            await state.set_state(None)
            ui_state = await nav_service.replace_current(
                state,
                route=result.next_route or return_route,
                payload=result.next_payload or return_payload,
                source_route=ui_state.source_route,
            )
            rendered = await screen_service.render(
                route=ui_state.current_route,
                payload=ui_state.payload,
                user_id=message.from_user.id,
                rev=ui_state.revision,
            )
    else:
        rendered = await screen_service.render_input_waiting(
            kind=kind,
            user_id=message.from_user.id,
            rev=ui_state.revision,
            error_text=result.error_text,
        )

    if ui_state.anchor_message_id is not None:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=ui_state.anchor_message_id,
            **msg.as_edit_kwargs(rendered.text, rendered.keyboard),
        )
