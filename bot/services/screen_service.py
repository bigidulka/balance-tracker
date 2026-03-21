"""Screen orchestration service for bot UI rendering."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram.utils.formatting import Text

from bot import messages as msg
from bot.config import settings
from bot.contracts.callbacks import (
    ROUTE_DEX,
    ROUTE_FUTURES_DETAIL,
    ROUTE_FUTURES_LIST,
    ROUTE_INTEGRATION_DETAIL,
    ROUTE_INTEGRATION_EXCHANGE_PICKER,
    ROUTE_INTEGRATIONS,
    ROUTE_INPUT,
    ROUTE_MAIN,
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
)
from bot.contracts.exchanges import SUPPORTED_CEX_EXCHANGE_CODES, SUPPORTED_CEX_EXCHANGE_LABELS
from bot.contracts.tariffs import get_tariff_plan, is_evm_chain
from bot.i18n import normalize_locale, t
from bot.keyboards import inline as kb
from bot.repositories.api_repository import ApiRepository
from bot.repositories.user_repository import UserRepository
from bot.services.runtime import clear_backend_auth_session
from bot.services.balance import filter_assets, parse_balances
from bot.api_client import debank_portfolio_client


@dataclass
class RenderedScreen:
    route: str
    payload: dict[str, Any]
    text: Text
    keyboard: Any


@dataclass
class InputProcessResult:
    success: bool
    error_text: str | None = None
    next_waiting: dict[str, Any] | None = None
    next_route: str | None = None
    next_payload: dict[str, Any] | None = None


@dataclass
class TariffSnapshot:
    plan_code: str
    plan_name: str
    cex_used: int
    cex_limit: int
    evm_used: int
    evm_limit: int
    refresh_interval_seconds: int
    can_refresh: bool
    retry_after_seconds: int
    allow_dex: bool
    last_refresh_at: datetime | None = None


class ScreenService:
    def __init__(self, api_repo: ApiRepository, user_repo: UserRepository) -> None:
        self.api_repo = api_repo
        self.user_repo = user_repo

    async def ensure_access(self, user_id: int) -> str | None:
        if not await self.user_repo.is_local_allowed(user_id):
            return msg.ACCESS_DENIED
        try:
            await self.api_repo.get_balances()
        except Exception as exc:
            text = str(exc)
            if "401" in text or "403" in text:
                await clear_backend_auth_session(user_id)
                return "Backend auth failed for this Telegram user."
        return None

    async def menu_context(self) -> tuple[bool, str | None, dict[str, Any], dict[str, Any]]:
        snapshot = await self._load_tariff_snapshot(None)
        capabilities = {
            "allow_dex": snapshot.allow_dex,
            "can_refresh": snapshot.can_refresh,
            "refresh": snapshot.can_refresh,
            "balances_refresh": snapshot.can_refresh,
        }
        allow_dex = snapshot.allow_dex
        plan_label = snapshot.plan_name
        throttling = {
            "retry_after_seconds": snapshot.retry_after_seconds,
            "min_refresh_interval_seconds": snapshot.refresh_interval_seconds,
        }
        return allow_dex, plan_label, capabilities, throttling

    async def render(self, *, route: str, payload: dict[str, Any], user_id: int, rev: int) -> RenderedScreen:
        if route == ROUTE_MAIN:
            return await self._render_main(user_id=user_id, rev=rev)
        if route == ROUTE_SPOT_LIST:
            page = self._safe_int(payload.get("page"), default=0)
            return await self._render_exchange_list(
                route=ROUTE_SPOT_LIST,
                detail_route=ROUTE_SPOT_DETAIL,
                title="Spot Balances",
                description="Spot balances grouped by connected exchange accounts.",
                exchange_key="spot_exchanges",
                total_key="spot_total",
                page=page,
                user_id=user_id,
                rev=rev,
            )
        if route == ROUTE_FUTURES_LIST:
            page = self._safe_int(payload.get("page"), default=0)
            return await self._render_exchange_list(
                route=ROUTE_FUTURES_LIST,
                detail_route=ROUTE_FUTURES_DETAIL,
                title="Futures Balances",
                description="Futures balances grouped by connected exchange accounts.",
                exchange_key="futures_exchanges",
                total_key="futures_total",
                page=page,
                user_id=user_id,
                rev=rev,
            )
        if route == ROUTE_SPOT_DETAIL:
            return await self._render_exchange_detail(
                route=ROUTE_SPOT_DETAIL,
                back_route=ROUTE_SPOT_DETAIL,
                title_suffix="Spot",
                exchange_key="spot_exchanges",
                exchange=str(payload.get("exchange") or ""),
                exchange_index=self._safe_int(payload.get("i"), default=-1),
                rev=rev,
                user_id=user_id,
            )
        if route == ROUTE_FUTURES_DETAIL:
            return await self._render_exchange_detail(
                route=ROUTE_FUTURES_DETAIL,
                back_route=ROUTE_FUTURES_DETAIL,
                title_suffix="Futures",
                exchange_key="futures_exchanges",
                exchange=str(payload.get("exchange") or ""),
                exchange_index=self._safe_int(payload.get("i"), default=-1),
                rev=rev,
                user_id=user_id,
            )
        if route == ROUTE_DEX:
            page = self._safe_int(payload.get("page"), default=0)
            wallet_index = self._safe_int(payload.get("i"), default=-1)
            return await self._render_dex(page=page, wallet_index=wallet_index, user_id=user_id, rev=rev)
        if route == ROUTE_TRANSACTIONS:
            page = self._safe_int(payload.get("page"), default=0)
            source_index = self._safe_int(payload.get("i"), default=-1)
            return await self._render_transactions(page=page, source_index=source_index, user_id=user_id, rev=rev)
        if route == ROUTE_INTEGRATIONS:
            page = self._safe_int(payload.get("page"), default=0)
            return await self._render_integrations(user_id=user_id, rev=rev, page=page)
        if route == ROUTE_INTEGRATION_DETAIL:
            integration_id = self._safe_int(payload.get("id"), default=-1)
            job_id = self._safe_int(payload.get("job_id"), default=-1)
            return await self._render_integration_detail(
                integration_id=integration_id,
                job_id=job_id if job_id >= 0 else None,
                user_id=user_id,
                rev=rev,
            )
        if route == ROUTE_INTEGRATION_EXCHANGE_PICKER:
            return await self._render_integration_exchange_picker(user_id=user_id, rev=rev)
        if route == ROUTE_PLAN:
            return await self._render_plan(user_id=user_id, rev=rev, reason=str(payload.get("reason") or ""))
        if route == ROUTE_PAYMENTS:
            invoice_id = self._safe_int(payload.get("id"), default=-1)
            return await self._render_payments(user_id=user_id, rev=rev, invoice_id=invoice_id if invoice_id > 0 else None)
        if route == ROUTE_ADMIN:
            return await self._render_admin(user_id=user_id, rev=rev)
        if route == ROUTE_ADMIN_USERS:
            page = self._safe_int(payload.get("page"), default=0)
            return await self._render_admin_users(user_id=user_id, rev=rev, page=page)
        if route == ROUTE_ADMIN_USER_DETAIL:
            return await self._render_admin_user_detail(
                user_id=user_id,
                rev=rev,
                target_user_id=self._safe_int(payload.get("user_id"), default=0),
                organization_id=self._safe_int(payload.get("organization_id"), default=0),
            )
        if route == ROUTE_INPUT:
            return await self.render_input_waiting(
                kind=str(payload.get("kind") or ""),
                user_id=user_id,
                rev=rev,
            )
        if route == ROUTE_SETTINGS:
            return await self._render_settings(user_id=user_id, rev=rev)
        return await self._render_main(user_id=user_id, rev=rev)

    async def refresh_main(self) -> None:
        await self.api_repo.refresh_balances()
        await self.api_repo.get_balances(force_update_cache=True)

    async def refresh_transactions(self, *, user_id: int) -> None:
        user_settings = await self.user_repo.get_settings(user_id)
        since_hours = int(user_settings.get("tx_since_hours", 24) or 24)
        await self.api_repo.refresh_transactions(since_hours=since_hours)

    async def apply_transactions_filter(self, *, user_id: int, field: str, value: str) -> None:
        user_settings = await self.user_repo.get_settings(user_id)
        if field == "tx_type" and value in {"all", "deposit", "withdrawal"}:
            user_settings["tx_type"] = value
        elif field == "tx_status" and value in {"all", "pending", "ok", "failed"}:
            user_settings["tx_status"] = value
        elif field == "tx_since_hours":
            user_settings["tx_since_hours"] = self._safe_int(value, default=24)
        elif field == "language":
            current = normalize_locale(user_settings.get("language"))
            user_settings["language"] = "en" if current == "ru" else "ru"
        await self.user_repo.save_settings(user_id, user_settings)

    async def reset_transactions_filters(self, *, user_id: int) -> None:
        user_settings = await self.user_repo.get_settings(user_id)
        user_settings["tx_type"] = "all"
        user_settings["tx_status"] = "all"
        user_settings["tx_service"] = "all"
        user_settings["tx_since_hours"] = 24
        await self.user_repo.save_settings(user_id, user_settings)

    async def update_hide_small(self, *, user_id: int) -> None:
        user_settings = await self.user_repo.get_settings(user_id)
        user_settings["hide_small"] = not bool(user_settings.get("hide_small"))
        await self.user_repo.save_settings(user_id, user_settings)

    async def apply_input_value(self, *, user_id: int, kind: str, raw_value: str) -> tuple[bool, str]:
        kind = decode_input_kind(kind)
        if kind == "tx_since_hours":
            value = self._safe_int(raw_value, default=-1)
            if value <= 0 or value > 24 * 30:
                return False, "Введите целое число часов от 1 до 720."
            user_settings = await self.user_repo.get_settings(user_id)
            user_settings["tx_since_hours"] = value
            await self.user_repo.save_settings(user_id, user_settings)
            return True, "Ок"
        return False, "Неизвестный тип ввода."

    def start_input_flow(
        self,
        *,
        kind: str,
        return_route: str,
        return_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized_kind = decode_input_kind(kind)
        waiting: dict[str, Any] = {
            "kind": normalized_kind,
            "return_route": return_route,
            "return_payload": dict(return_payload or {}),
        }
        if normalized_kind == "integration_cex_name":
            waiting["draft"] = {"provider": "ccxt", "kind": "cex"}
        elif normalized_kind in {"integration_dex_name", "integration_dex_wallet_address"}:
            waiting["draft"] = {"provider": "okx_wallet", "kind": "dex"}
        elif normalized_kind == "integration_rename":
            integration_id = self._safe_int((return_payload or {}).get("id"), default=-1)
            waiting["draft"] = {"id": integration_id}
        return waiting

    def start_exchange_account_ref_flow(
        self,
        *,
        exchange_code: str,
        return_route: str,
        return_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "kind": "integration_cex_account_ref",
            "return_route": return_route,
            "return_payload": dict(return_payload or {}),
            "draft": {
                "provider": "ccxt",
                "kind": "cex",
                "exchange_code": exchange_code.strip().lower(),
            },
        }

    async def process_input_value(
        self,
        *,
        user_id: int,
        waiting: dict[str, Any],
        raw_value: str,
    ) -> InputProcessResult:
        kind = decode_input_kind(str(waiting.get("kind") or ""))
        value = raw_value.strip()

        if kind == "tx_since_hours":
            ok, reason = await self.apply_input_value(user_id=user_id, kind=kind, raw_value=value)
            return InputProcessResult(success=ok, error_text=None if ok else reason)

        if kind == "promo_code":
            return await self._process_promo_code_input(raw_value=value)

        if kind.startswith("integration_"):
            return await self._process_integration_input(
                user_id=user_id,
                waiting=waiting,
                kind=kind,
                raw_value=value,
            )

        return InputProcessResult(success=False, error_text="Unknown input type.")

    async def activate_integration(self, integration_id: int) -> None:
        await self.api_repo.activate_integration(integration_id)

    async def deactivate_integration(self, integration_id: int) -> None:
        await self.api_repo.deactivate_integration(integration_id)

    async def refresh_integration(self, integration_id: int) -> int | None:
        payload = await self.api_repo.refresh_integration(integration_id)
        raw = payload.get("job_id") if isinstance(payload, dict) else None
        return raw if isinstance(raw, int) else None

    async def delete_integration(self, integration_id: int) -> None:
        await self.api_repo.delete_integration(integration_id)

    async def is_admin_user(self, user_id: int) -> bool:
        return await self.user_repo.is_local_admin(user_id)

    async def create_topup_invoice(self, amount_usd: float) -> dict[str, Any]:
        return await self.api_repo.create_payment_invoice(amount_usd, invoice_type="balance_topup")

    async def create_plan_invoice(self, plan_code: str, amount_usd: float | None = None) -> dict[str, Any]:
        return await self.api_repo.create_payment_invoice(
            amount_usd,
            invoice_type="plan_purchase",
            plan_code=plan_code,
        )

    async def switch_plan(self, plan_code: str) -> dict[str, Any]:
        return await self.api_repo.switch_billing_plan(plan_code)

    async def refresh_payment_invoice(self, invoice_id: int) -> dict[str, Any]:
        return await self.api_repo.refresh_payment_invoice(invoice_id)

    async def render_input_waiting(self, *, kind: str, user_id: int, rev: int, error_text: str | None = None) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        return RenderedScreen(
            route="input",
            payload={"kind": kind},
            text=msg.input_waiting_text(kind, error_text=error_text, locale=locale),
            keyboard=kb.input_waiting(rev=rev, locale=locale),
        )

    async def _load_billing_payload(self) -> dict[str, Any]:
        try:
            payload = await self.api_repo.get_billing_current()
            if payload:
                return payload
        except Exception:
            pass
        try:
            fallback = await self.api_repo.get_capabilities()
        except Exception:
            return {}
        return fallback if isinstance(fallback, dict) else {}

    @staticmethod
    def _count_usage(integrations: list[dict[str, Any]]) -> tuple[int, int]:
        cex = 0
        evm = 0
        for item in integrations:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("is_active", True)):
                continue
            kind = str(item.get("kind") or "").strip().lower()
            if kind == "cex":
                cex += 1
                continue
            if kind == "dex" and is_evm_chain(str(item.get("chain") or "")):
                evm += 1
        return cex, evm

    @staticmethod
    def _refresh_state(last_refresh_at: datetime | None, refresh_interval_seconds: int) -> tuple[bool, int]:
        normalized = ScreenService._coerce_datetime(last_refresh_at)
        if normalized is None:
            return True, 0
        elapsed_seconds = int((datetime.now(timezone.utc) - normalized.astimezone(timezone.utc)).total_seconds())
        if elapsed_seconds >= refresh_interval_seconds:
            return True, 0
        return False, max(refresh_interval_seconds - elapsed_seconds, 1)

    async def _load_tariff_snapshot(self, user_id: int | None) -> TariffSnapshot:
        billing = await self._load_billing_payload()
        plan_payload = billing.get("plan") if isinstance(billing.get("plan"), dict) else {}
        plan_code = str(plan_payload.get("code") or billing.get("plan_code") or "free").strip().lower()
        if plan_code == "full":
            plan_code = "pro"
        tariff = get_tariff_plan(plan_code)
        policy = billing.get("policy") if isinstance(billing.get("policy"), dict) else {}
        limits = billing.get("limits") if isinstance(billing.get("limits"), dict) else {}
        usage = billing.get("usage") if isinstance(billing.get("usage"), dict) else {}
        features = policy.get("features") if isinstance(policy.get("features"), dict) else {}
        capabilities = billing.get("capabilities") if isinstance(billing.get("capabilities"), dict) else {}

        cex_state = usage.get("cex") if isinstance(usage.get("cex"), dict) else {}
        evm_state = usage.get("evm") if isinstance(usage.get("evm"), dict) else {}
        cex_used = self._safe_int(cex_state.get("active"), default=-1)
        evm_used = self._safe_int(evm_state.get("active"), default=-1)
        cex_limit = self._safe_int(limits.get("max_cex_accounts"), default=0)
        evm_limit = self._safe_int(limits.get("max_evm_wallets"), default=0)
        refresh_interval_seconds = self._safe_int(
            (billing.get("background") if isinstance(billing.get("background"), dict) else {}).get("refresh_interval_seconds"),
            default=0,
        )

        if cex_used < 0 or evm_used < 0:
            try:
                integrations = await self.api_repo.get_integrations()
            except Exception:
                integrations = []
            fallback_cex_used, fallback_evm_used = self._count_usage(integrations)
            if cex_used < 0:
                cex_used = fallback_cex_used
            if evm_used < 0:
                evm_used = fallback_evm_used
        if cex_limit <= 0:
            cex_limit = tariff.max_cex_accounts
        if evm_limit < 0:
            evm_limit = 0
        if evm_limit == 0 and "max_evm_wallets" not in limits:
            evm_limit = tariff.max_evm_wallets
        if refresh_interval_seconds <= 0:
            refresh_interval_seconds = tariff.refresh_interval_seconds

        allow_dex_raw = capabilities.get("allow_dex")
        if isinstance(allow_dex_raw, bool):
            allow_dex = allow_dex_raw
        else:
            features_allow_dex = features.get("allow_dex")
            allow_dex = bool(features_allow_dex) if isinstance(features_allow_dex, bool) else self._allow_dex(capabilities)
        last_refresh_at = self._coerce_datetime(billing.get("last_refresh_at"))
        refresh_from_capabilities = capabilities.get("can_refresh", capabilities.get("refresh"))
        retry_after_seconds = self._safe_int(
            (billing.get("throttling") if isinstance(billing.get("throttling"), dict) else {}).get("retry_after_seconds"),
            default=0,
        )
        if isinstance(refresh_from_capabilities, bool):
            can_refresh = refresh_from_capabilities
        else:
            can_refresh, retry_after_seconds = self._refresh_state(last_refresh_at, refresh_interval_seconds)
        if can_refresh:
            retry_after_seconds = 0

        return TariffSnapshot(
            plan_code=plan_code,
            plan_name=(
                tariff.name
                if str(plan_payload.get("name") or "").strip().lower() in {"", "full"}
                else str(plan_payload.get("name"))
            ),
            cex_used=cex_used,
            cex_limit=cex_limit,
            evm_used=evm_used,
            evm_limit=evm_limit,
            refresh_interval_seconds=refresh_interval_seconds,
            can_refresh=can_refresh,
            retry_after_seconds=retry_after_seconds,
            allow_dex=allow_dex,
            last_refresh_at=last_refresh_at,
        )

    async def _render_plan(self, *, user_id: int, rev: int, reason: str = "") -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        snapshot = await self._load_tariff_snapshot(user_id)
        billing = await self._load_billing_payload()
        wallet = billing.get("wallet") if isinstance(billing.get("wallet"), dict) else {}
        try:
            plans_payload = await self.api_repo.get_billing_plans()
        except Exception:
            plans_payload = {}
        plans = plans_payload.get("plans") if isinstance(plans_payload.get("plans"), list) else []
        reason_text = reason.strip()
        if reason_text == "cex_limit_reached":
            reason_text = f"{t(locale, 'cex_accounts')}: {snapshot.cex_used}/{snapshot.cex_limit} {t(locale, 'limit_reached')}"
        elif reason_text == "evm_limit_reached":
            reason_text = f"{t(locale, 'evm_wallets')}: {snapshot.evm_used}/{snapshot.evm_limit} {t(locale, 'limit_reached')}"

        text = msg.plan_text(
            plan_code=snapshot.plan_code,
            plan_name=snapshot.plan_name,
            cex_used=snapshot.cex_used,
            cex_limit=snapshot.cex_limit,
            evm_used=snapshot.evm_used,
            evm_limit=snapshot.evm_limit,
            refresh_interval_seconds=snapshot.refresh_interval_seconds,
            can_refresh=snapshot.can_refresh,
            retry_after_seconds=snapshot.retry_after_seconds,
            wallet_balance_usd=float(wallet.get("available") or 0.0),
            plans=[item for item in plans if isinstance(item, dict)],
            reason=reason_text or None,
            locale=locale,
        )
        return RenderedScreen(
            route=ROUTE_PLAN,
            payload={"reason": reason_text} if reason_text else {},
            text=text,
            keyboard=kb.plan_screen(
                rev=rev,
                locale=locale,
                plans=[item for item in plans if isinstance(item, dict)],
                current_plan_code=snapshot.plan_code,
            ),
        )

    async def _render_payments(self, *, user_id: int, rev: int, invoice_id: int | None) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        balance_payload = await self.api_repo.get_billing_balance()
        invoices_payload = await self.api_repo.list_payment_invoices()
        invoices = invoices_payload.get("items") if isinstance(invoices_payload.get("items"), list) else []
        active_invoice = next(
            (
                item for item in invoices
                if isinstance(item, dict) and (invoice_id is None or int(item.get("id") or 0) == invoice_id)
            ),
            None,
        )
        latest_invoice_dt = max(
            (
                self._coerce_datetime(item.get("created_at"))
                for item in invoices
                if isinstance(item, dict) and self._coerce_datetime(item.get("created_at")) is not None
            ),
            default=None,
        )
        return RenderedScreen(
            route=ROUTE_PAYMENTS,
            payload={"id": int(active_invoice.get("id"))} if isinstance(active_invoice, dict) and active_invoice.get("id") else {},
            text=msg.payments_text(
                balance_usd=float(balance_payload.get("available") or 0.0),
                invoices=[item for item in invoices if isinstance(item, dict)],
                active_invoice=active_invoice if isinstance(active_invoice, dict) else None,
                locale=locale,
            ),
            keyboard=kb.payments_screen(
                invoice_id=int(active_invoice.get("id")) if isinstance(active_invoice, dict) and active_invoice.get("id") else None,
                rev=rev,
                locale=locale,
                refresh_time_label=self._refresh_time_label(dt=latest_invoice_dt, locale=locale),
            ),
        )

    async def _render_admin(self, *, user_id: int, rev: int) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        if not await self.is_admin_user(user_id):
            return RenderedScreen(route=ROUTE_ADMIN, payload={}, text=msg.gated_feature_text("Admin access required.", locale=locale), keyboard=kb.admin_screen(rev=rev, locale=locale))
        return RenderedScreen(
            route=ROUTE_ADMIN,
            payload={},
            text=msg.admin_panel_text(locale=locale),
            keyboard=kb.admin_screen(rev=rev, locale=locale),
        )

    async def _render_admin_users(self, *, user_id: int, rev: int, page: int) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        if not await self.is_admin_user(user_id):
            return RenderedScreen(route=ROUTE_ADMIN_USERS, payload={"page": 0}, text=msg.gated_feature_text("Admin access required.", locale=locale), keyboard=kb.admin_screen(rev=rev, locale=locale))
        page = max(0, page)
        limit = 8
        payload = await self.api_repo.admin_list_users(limit=limit, offset=page * limit)
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        total = self._safe_int(payload.get("total"), default=len(items))
        return RenderedScreen(
            route=ROUTE_ADMIN_USERS,
            payload={"page": page},
            text=msg.admin_users_text(items=[item for item in items if isinstance(item, dict)], total=total, page=page, page_size=limit, locale=locale),
            keyboard=kb.admin_users(
                items=[item for item in items if isinstance(item, dict)],
                page=page,
                has_prev=page > 0,
                has_next=((page + 1) * limit) < total,
                rev=rev,
                locale=locale,
            ),
        )

    async def _render_admin_user_detail(
        self,
        *,
        user_id: int,
        rev: int,
        target_user_id: int,
        organization_id: int,
    ) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        if not await self.is_admin_user(user_id):
            return RenderedScreen(route=ROUTE_ADMIN_USER_DETAIL, payload={}, text=msg.gated_feature_text("Admin access required.", locale=locale), keyboard=kb.admin_screen(rev=rev, locale=locale))
        payload = await self.api_repo.admin_get_user_detail(
            user_id=target_user_id,
            organization_id=organization_id,
        )
        return RenderedScreen(
            route=ROUTE_ADMIN_USER_DETAIL,
            payload={"user_id": target_user_id, "organization_id": organization_id},
            text=msg.admin_user_detail_text(payload if isinstance(payload, dict) else {}, locale=locale),
            keyboard=kb.admin_user_detail(
                user_id=target_user_id,
                organization_id=organization_id,
                rev=rev,
                locale=locale,
            ),
        )

    async def can_add_cex_account(self, *, user_id: int) -> tuple[bool, str | None]:
        locale = await self._locale_for_user_id(user_id)
        snapshot = await self._load_tariff_snapshot(user_id)
        if snapshot.cex_used >= snapshot.cex_limit:
            return False, f"{t(locale, 'cex_accounts')}: {snapshot.cex_used}/{snapshot.cex_limit} {t(locale, 'limit_reached')}"
        return True, None

    async def can_add_evm_wallet(self, *, user_id: int, chain: str) -> tuple[bool, str | None]:
        if not is_evm_chain(chain):
            return True, None
        locale = await self._locale_for_user_id(user_id)
        snapshot = await self._load_tariff_snapshot(user_id)
        if snapshot.evm_used >= snapshot.evm_limit:
            return False, f"{t(locale, 'evm_wallets')}: {snapshot.evm_used}/{snapshot.evm_limit} {t(locale, 'limit_reached')}"
        return True, None

    def _refresh_time_label(
        self,
        *,
        dt: datetime | str | None = None,
        locale: str = "ru",
        fallback_to_now: bool = False,
    ) -> str | None:
        resolved = self._coerce_datetime(dt)
        if resolved is None and fallback_to_now:
            resolved = datetime.now(timezone.utc)
        if resolved is None:
            return None
        return self._format_relative_elapsed(resolved, locale=locale)

    @staticmethod
    def _coerce_datetime(value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        return None

    @classmethod
    def _format_relative_elapsed(cls, dt: datetime, *, locale: str) -> str:
        now = datetime.now(timezone.utc)
        elapsed = max(0, int((now - dt.astimezone(timezone.utc)).total_seconds()))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            compact = f"{hours}:{minutes:02}:{seconds:02}"
        elif minutes > 0:
            compact = f"{minutes}:{seconds:02}"
        else:
            compact = f"{seconds}s"
        return f"{compact} {t(locale, 'ago')}"

    def _latest_balance_update(self, data: dict[str, Any]) -> datetime | None:
        latest: datetime | None = None
        services = data.get("services", [])
        if not isinstance(services, list):
            return None
        for svc in services:
            if not isinstance(svc, dict):
                continue
            updated_at = self._coerce_datetime(svc.get("updated_at"))
            if updated_at is None:
                continue
            if latest is None or updated_at > latest:
                latest = updated_at
        return latest

    def _latest_exchange_update(self, data: dict[str, Any], exchange: str) -> datetime | None:
        services = data.get("services", [])
        if not isinstance(services, list):
            return None
        normalized = exchange.strip().lower()
        latest: datetime | None = None
        for svc in services:
            if not isinstance(svc, dict):
                continue
            service_name = str(svc.get("service") or "").strip().lower()
            if normalized and service_name != normalized:
                continue
            updated_at = self._coerce_datetime(svc.get("updated_at"))
            if updated_at is None:
                continue
            if latest is None or updated_at > latest:
                latest = updated_at
        return latest

    async def _render_main(self, *, user_id: int, rev: int) -> RenderedScreen:
        allow_dex, plan_label, capabilities, throttling = await self.menu_context()
        can_refresh = self._refresh_allowed(capabilities)
        retry_after = int(throttling.get("retry_after_seconds") or 0)
        locale = await self._locale_for_user_id(user_id)
        refresh_time_label: str | None = None

        try:
            summary = await self.api_repo.get_dashboard_summary()
            if settings.no_backend_ui_mode:
                summary["plan"] = summary.get("plan") or {"name": "UI Preview", "code": "ui_preview"}
                summary["freshness"] = summary.get("freshness") or "mock_preview_mode"
            refresh_time_label = self._refresh_time_label(
                dt=summary.get("latest_updated_at"),
                locale=locale,
            )
            text = msg.dashboard_text(summary, locale=locale)
        except Exception:
            data = await self.api_repo.get_balances()
            parsed = parse_balances(data)
            freshness = msg.format_timestamp(data, locale=locale)
            refresh_state = msg.format_refresh_state(capabilities, throttling, locale=locale)
            refresh_time_label = self._refresh_time_label(
                dt=self._latest_balance_update(data),
                locale=locale,
            )
            text = msg.balance_overview_text(parsed, freshness, refresh_state, locale=locale)

        keyboard = kb.main_menu(
            rev=rev,
            locale=locale,
            allow_dex=allow_dex,
            plan_label=plan_label,
            can_refresh=can_refresh,
            retry_after_seconds=retry_after,
            refresh_time_label=refresh_time_label,
        )
        return RenderedScreen(route=ROUTE_MAIN, payload={}, text=text, keyboard=keyboard)

    async def _render_exchange_list(
        self,
        *,
        route: str,
        detail_route: str,
        title: str,
        description: str,
        exchange_key: str,
        total_key: str,
        page: int,
        user_id: int,
        rev: int,
    ) -> RenderedScreen:
        data = await self.api_repo.get_balances()
        parsed = parse_balances(data)
        user_settings = await self.user_repo.get_settings(user_id)
        locale = self._locale_from_settings(user_settings)
        refresh_time_label = self._refresh_time_label(
            dt=self._latest_balance_update(data),
            locale=locale,
        )

        lines, exchanges = self._select_exchange_rows(
            parsed.get(exchange_key, {}),
            hide_small=bool(user_settings.get("hide_small")),
        )

        per_page = 8
        total_count = len(exchanges)
        total_pages = max(1, math.ceil(total_count / per_page))
        max_page = max(0, total_pages - 1)
        page = max(0, min(page, max_page))
        start = page * per_page
        end = start + per_page
        page_lines = lines[start:end]

        text = msg.exchange_list_text(
            title=t(locale, "spot_balances") if route == ROUTE_SPOT_LIST else t(locale, "futures_balances"),
            total_usd=float(parsed.get(total_key, 0) or 0),
            description=t(locale, "spot_description") if route == ROUTE_SPOT_LIST else t(locale, "futures_description"),
            lines=page_lines,
            visible_count=len(page_lines),
            total_count=total_count,
            page=page,
            total_pages=total_pages,
            locale=locale,
        )
        keyboard = (
            kb.spot_exchanges(exchanges, page=page, per_page=per_page, rev=rev, locale=locale, refresh_time_label=refresh_time_label)
            if route == ROUTE_SPOT_LIST
            else kb.futures_exchanges(exchanges, page=page, per_page=per_page, rev=rev, locale=locale, refresh_time_label=refresh_time_label)
        )
        return RenderedScreen(route=route, payload={"page": page}, text=text, keyboard=keyboard)

    async def _render_exchange_detail(
        self,
        *,
        route: str,
        back_route: str,
        title_suffix: str,
        exchange_key: str,
        exchange: str,
        exchange_index: int,
        rev: int,
        user_id: int,
    ) -> RenderedScreen:
        data = await self.api_repo.get_balances()
        parsed = parse_balances(data)
        user_settings = await self.user_repo.get_settings(user_id)
        locale = self._locale_from_settings(user_settings)
        refresh_time_label = self._refresh_time_label(
            dt=self._latest_exchange_update(data, exchange),
            locale=locale,
        )

        exchanges_map = parsed.get(exchange_key, {}) or {}
        if not exchange and exchange_index >= 0:
            sorted_names = [
                name
                for name, acc_row in sorted(
                    exchanges_map.items(),
                    key=lambda item: item[1].get("total_usd", 0),
                    reverse=True,
                )
                if not bool(user_settings.get("hide_small"))
                or float(acc_row.get("total_usd", 0) or 0) >= settings.hide_small_balance_threshold
            ]
            if 0 <= exchange_index < len(sorted_names):
                exchange = sorted_names[exchange_index]

        acc = exchanges_map.get(exchange, {})
        assets = sorted(
            acc.get("assets", []),
            key=lambda item: item.get("value_usd", 0),
            reverse=True,
        )
        assets = filter_assets(assets, bool(user_settings.get("hide_small")))

        shown_assets = assets[:20]
        has_more = len(assets) > len(shown_assets)
        more_count = max(0, len(assets) - len(shown_assets))

        text = msg.exchange_detail_text(
            title=f"{exchange} • {title_suffix}",
            total_usd=float(acc.get("total_usd", 0) or 0),
            description=f"{title_suffix} account asset breakdown for the selected exchange.",
            assets=shown_assets,
            has_more=has_more,
            more_count=more_count,
            locale=locale,
        )
        return RenderedScreen(
            route=route,
            payload={"exchange": exchange},
            text=text,
            keyboard=kb.exchange_detail(back_route, rev=rev, locale=locale, refresh_time_label=refresh_time_label),
        )

    async def _render_dex(self, *, page: int, wallet_index: int, user_id: int, rev: int) -> RenderedScreen:
        data = await self.api_repo.get_balances()
        parsed = parse_balances(data)
        user_settings = await self.user_repo.get_settings(user_id)
        locale = self._locale_from_settings(user_settings)
        refresh_time_label = self._refresh_time_label(
            dt=self._latest_balance_update(data),
            locale=locale,
        )

        try:
            integrations = await self.api_repo.get_integrations()
        except Exception:
            integrations = []
        name_map = self._build_service_name_map(integrations)

        dex_wallets = parsed.get("dex_wallets") or {}
        wallet_items = self._select_wallet_rows(
            dex_wallets,
            hide_small=bool(user_settings.get("hide_small")),
            name_map=name_map,
        )
        if wallet_index < 0:
            per_page = 8
            total_count = len(wallet_items)
            total_pages = max(1, math.ceil(total_count / per_page))
            max_page = max(0, total_pages - 1)
            page = max(0, min(page, max_page))
            start = page * per_page
            end = start + per_page
            page_rows = wallet_items[start:end]

            # Build body lines with balance suffix for display (buttons use name-only page_rows)
            wallet_service_order = self._wallet_service_order(
                dex_wallets,
                hide_small=bool(user_settings.get("hide_small")),
            )
            body_lines: list[str] = []
            for svc in wallet_service_order[start:end]:
                display = name_map.get(svc) or self._display_name_for_service(svc, parsed, include_total=False)
                total = float(dex_wallets.get(svc, {}).get("total_usd", 0) or 0)
                body_lines.append(f"{display}: {msg.format_usd(total)}")

            text = msg.dex_wallets_text(
                total_usd=float(parsed.get("dex_total", 0) or 0),
                lines=body_lines,
                visible_count=len(page_rows),
                total_count=total_count,
                page=page,
                total_pages=total_pages,
                locale=locale,
            )
            keyboard = kb.dex_wallets(
                wallet_items,
                page=page,
                per_page=per_page,
                rev=rev,
                locale=locale,
                refresh_time_label=refresh_time_label,
            )
            return RenderedScreen(route=ROUTE_DEX, payload={"page": page}, text=text, keyboard=keyboard)

        wallet_services = self._wallet_service_order(
            dex_wallets,
            hide_small=bool(user_settings.get("hide_small")),
        )
        if not (0 <= wallet_index < len(wallet_services)):
            return await self._render_dex(page=0, wallet_index=-1, user_id=user_id, rev=rev)
        service = wallet_services[wallet_index]
        wallet = dex_wallets.get(service, {})
        assets = sorted(
            wallet.get("assets", []),
            key=lambda item: item.get("value_usd", 0),
            reverse=True,
        )
        assets = filter_assets(assets, bool(user_settings.get("hide_small")))
        per_page = 20
        total_pages = max(1, math.ceil(len(assets) / per_page))
        page = max(0, min(page, total_pages - 1))
        start = page * per_page
        end = start + per_page
        page_assets = assets[start:end]
        label = name_map.get(service) or self._display_name_for_service(service, parsed, include_total=False)
        address = service.removeprefix("okx_wallet_")

        # Enrich with chain breakdown + full token names from debank-sdk portfolio API
        portfolio: dict | None = None
        try:
            portfolio = await debank_portfolio_client.get_portfolio(
                user_id=user_id,
                address=address,
                page=page,
            )
        except Exception:
            pass

        chains: list[dict] = portfolio.get("chains") or [] if portfolio else []
        portfolio_tokens: list[dict] = portfolio.get("tokens") or [] if portfolio else []
        portfolio_total_tokens: int = int(portfolio.get("totalTokens") or 0) if portfolio else 0
        has_prev_page: bool = bool(portfolio.get("hasPrevPage")) if portfolio else False
        has_next_page: bool = bool(portfolio.get("hasNextPage")) if portfolio else False

        text = msg.dex_wallet_detail_text(
            wallet_label=label,
            address=address,
            total_usd=float(wallet.get("total_usd", 0) or 0),
            assets=page_assets,
            has_more=len(assets) > end,
            more_count=max(0, len(assets) - end),
            page=page,
            total_pages=total_pages,
            total_asset_count=portfolio_total_tokens or len(assets),
            chains=chains,
            portfolio_tokens=portfolio_tokens,
            locale=locale,
        )
        return RenderedScreen(
            route=ROUTE_DEX,
            payload={"i": wallet_index, "page": page},
            text=text,
            keyboard=kb.dex_wallet_detail(
                wallet_index,
                page=page,
                total_pages=total_pages,
                rev=rev,
                locale=locale,
                refresh_time_label=refresh_time_label,
            ),
        )

    async def _render_transactions(self, *, page: int, source_index: int, user_id: int, rev: int) -> RenderedScreen:
        limit = 10
        page = max(0, page)
        offset = page * limit

        user_settings = await self.user_repo.get_settings(user_id)
        locale = self._locale_from_settings(user_settings)
        tx_since_hours = int(user_settings.get("tx_since_hours", 24) or 24)

        now = datetime.now(timezone.utc)
        start_date = now - timedelta(hours=tx_since_hours)

        if source_index < 0:
            balances = await self.api_repo.get_balances()
            parsed = parse_balances(balances)
            integrations = await self.api_repo.get_integrations()
            name_map = self._build_service_name_map(integrations)
            source_rows = self._transaction_source_rows(parsed, integrations, name_map=name_map)
            per_page = 8
            total_count = len(source_rows)
            total_pages = max(1, math.ceil(total_count / per_page))
            max_page = max(0, total_pages - 1)
            page = max(0, min(page, max_page))
            start = page * per_page
            end = start + per_page
            page_rows = source_rows[start:end]
            refresh_time_label = self._refresh_time_label(
                dt=self._latest_balance_update(balances),
                locale=locale,
            )
            text = msg.transaction_sources_text(
                lines=[],
                visible_count=len(page_rows),
                total_count=total_count,
                page=page,
                total_pages=total_pages,
                locale=locale,
            )
            keyboard = kb.transaction_sources(
                source_rows,
                page=page,
                per_page=per_page,
                rev=rev,
                locale=locale,
                refresh_time_label=refresh_time_label,
            )
            return RenderedScreen(route=ROUTE_TRANSACTIONS, payload={"page": page}, text=text, keyboard=keyboard)

        balances = await self.api_repo.get_balances()
        parsed = parse_balances(balances)
        integrations = await self.api_repo.get_integrations()
        source_services = self._transaction_source_services(parsed, integrations)
        if not (0 <= source_index < len(source_services)):
            return await self._render_transactions(page=0, source_index=-1, user_id=user_id, rev=rev)
        service = source_services[source_index]
        integration_id = self._integration_id_from_service_key(service)
        response = await self.api_repo.get_transactions(
            limit=limit,
            offset=offset,
            tx_type=None,
            tx_status=None,
            tx_service=self._base_service_name(service),
            integration_id=integration_id,
            start_date=start_date,
        )

        rows = response.get("transactions", []) if isinstance(response, dict) else []
        if not isinstance(rows, list):
            rows = []
        refresh_time_label = self._refresh_time_label(
            dt=response.get("refreshed_at") if isinstance(response, dict) else None,
            locale=locale,
        )

        has_prev = page > 0
        has_next = len(rows) == limit

        text = msg.transaction_detail_text(
            source_label=self._display_name_for_service(service, parsed),
            rows=rows,
            page=page,
            locale=locale,
        )
        keyboard = kb.transaction_detail(
            source_index,
            page=page,
            has_prev=has_prev,
            has_next=has_next,
            rev=rev,
            locale=locale,
            refresh_time_label=refresh_time_label,
        )
        return RenderedScreen(route=ROUTE_TRANSACTIONS, payload={"i": source_index, "page": page}, text=text, keyboard=keyboard)

    async def _render_integrations(self, *, user_id: int, rev: int, page: int = 0) -> RenderedScreen:
        integrations = await self.api_repo.get_integrations()
        locale = await self._locale_for_user_id(user_id)
        snapshot = await self._load_tariff_snapshot(user_id)
        per_page = 8
        total = len(integrations)
        total_pages = max(1, math.ceil(total / per_page))
        page = max(0, min(page, total_pages - 1))
        text = msg.integrations_text(
            integrations,
            page=page,
            per_page=per_page,
            cex_used=snapshot.cex_used,
            cex_limit=snapshot.cex_limit,
            evm_used=snapshot.evm_used,
            evm_limit=snapshot.evm_limit,
            locale=locale,
        )
        latest_updated_at = max(
            (
                dt
                for dt in (self._coerce_datetime(item.get("updated_at")) for item in integrations if isinstance(item, dict))
                if dt is not None
            ),
            default=None,
        )
        return RenderedScreen(
            route=ROUTE_INTEGRATIONS,
            payload={"page": page},
            text=text,
            keyboard=kb.integrations(
                integrations,
                rev=rev,
                page=page,
                per_page=per_page,
                locale=locale,
                refresh_time_label=self._refresh_time_label(dt=latest_updated_at, locale=locale),
            ),
        )

    async def _render_integration_exchange_picker(self, *, user_id: int, rev: int) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        return RenderedScreen(
            route=ROUTE_INTEGRATION_EXCHANGE_PICKER,
            payload={},
            text=msg.integration_exchange_picker_text(locale=locale),
            keyboard=kb.integration_exchange_picker(rev=rev, locale=locale),
        )

    async def _render_integration_detail(self, *, integration_id: int, job_id: int | None, user_id: int, rev: int) -> RenderedScreen:
        integrations = await self.api_repo.get_integrations()
        item = next((i for i in integrations if int(i.get("id", -1)) == integration_id), None)
        if item is None:
            return await self._render_integrations(user_id=user_id, rev=rev)
        locale = await self._locale_for_user_id(user_id)

        text = msg.integration_detail_text(item, job_id=job_id, locale=locale)
        return RenderedScreen(
            route=ROUTE_INTEGRATION_DETAIL,
            payload={"id": integration_id} | ({"job_id": job_id} if job_id is not None else {}),
            text=text,
            keyboard=kb.integration_actions(
                integration_id,
                is_active=bool(item.get("is_active")),
                rev=rev,
                locale=locale,
                refresh_time_label=self._refresh_time_label(dt=item.get("updated_at"), locale=locale),
            ),
        )

    async def _render_settings(self, *, user_id: int, rev: int) -> RenderedScreen:
        user_settings = await self.user_repo.get_settings(user_id)
        locale = self._locale_from_settings(user_settings)
        text = msg.settings_text(
            hide_small=bool(user_settings.get("hide_small")),
            threshold=settings.hide_small_balance_threshold,
            language=locale,
            locale=locale,
        )
        return RenderedScreen(
            route=ROUTE_SETTINGS,
            payload={},
            text=text,
            keyboard=kb.settings(
                bool(user_settings.get("hide_small")),
                language=locale,
                rev=rev,
                locale=locale,
                is_admin=await self.is_admin_user(user_id),
            ),
        )

    async def _process_promo_code_input(self, *, raw_value: str) -> InputProcessResult:
        if not raw_value:
            return InputProcessResult(success=False, error_text="Enter promo code.")
        try:
            await self.api_repo.redeem_promo_code(raw_value)
        except Exception as exc:
            return InputProcessResult(success=False, error_text=str(exc))
        return InputProcessResult(success=True, next_route=ROUTE_PLAN, next_payload={})

    async def _process_integration_input(
        self,
        *,
        user_id: int,
        waiting: dict[str, Any],
        kind: str,
        raw_value: str,
    ) -> InputProcessResult:
        return_route = str(waiting.get("return_route") or ROUTE_INTEGRATIONS)
        return_payload = waiting.get("return_payload") if isinstance(waiting.get("return_payload"), dict) else {}
        draft = waiting.get("draft") if isinstance(waiting.get("draft"), dict) else {}
        draft = dict(draft)

        if kind == "integration_cex_account_ref":
            if not raw_value:
                return InputProcessResult(success=False, error_text="Enter account reference, for example main.")
            exchange_code = str(draft.get("exchange_code") or "").strip().lower()
            if exchange_code not in SUPPORTED_CEX_EXCHANGE_CODES:
                return InputProcessResult(success=False, error_text="Choose a supported exchange first.")
            draft["account_ref"] = raw_value
            if not draft.get("name"):
                exchange_label = SUPPORTED_CEX_EXCHANGE_LABELS.get(exchange_code, exchange_code.upper())
                draft["name"] = f"{exchange_label} {raw_value}"
            return await self._create_integration(draft)

        if kind == "integration_dex_wallet_address":
            if len(raw_value) < 8:
                return InputProcessResult(success=False, error_text="Enter a full wallet address.")
            draft["wallet_address"] = raw_value
            if self._looks_like_evm_address(raw_value):
                allowed, reason = await self.can_add_evm_wallet(user_id=user_id, chain="ethereum")
                if not allowed:
                    return InputProcessResult(success=False, error_text=reason)
                draft["chain"] = "ethereum"
                if not draft.get("name"):
                    short_address = raw_value if len(raw_value) <= 14 else f"{raw_value[:6]}...{raw_value[-4:]}"
                    draft["name"] = f"EVM {short_address}"
                return await self._create_integration(draft)
            return InputProcessResult(
                success=True,
                next_waiting=self._build_waiting_input(
                    kind="integration_dex_chain",
                    draft=draft,
                    return_route=return_route,
                    return_payload=return_payload,
                ),
            )

        if kind == "integration_dex_chain":
            value = raw_value.lower()
            if not value or not re.fullmatch(r"[a-z0-9_-]+", value):
                return InputProcessResult(success=False, error_text="Use chain like ethereum, arbitrum, solana or ton.")
            draft["chain"] = value
            if not draft.get("name"):
                address = str(draft.get("wallet_address") or "")
                short_address = address if len(address) <= 14 else f"{address[:6]}...{address[-4:]}"
                draft["name"] = f"{value.upper()} {short_address}"
            return await self._create_integration(draft)

        if kind == "integration_rename":
            if not raw_value:
                return InputProcessResult(success=False, error_text="Enter a new name.")
            integration_id = self._safe_int(draft.get("id"), default=-1)
            if integration_id < 0:
                return InputProcessResult(success=False, error_text="Integration not found.")
            try:
                await self.api_repo.update_integration(integration_id, raw_value)
            except Exception as exc:
                return InputProcessResult(success=False, error_text=self._integration_error_text(exc))
            return InputProcessResult(
                success=True,
                next_route=ROUTE_INTEGRATION_DETAIL,
                next_payload={"id": integration_id},
            )

        return InputProcessResult(success=False, error_text="Unknown integration input step.")

    async def _create_integration(self, payload: dict[str, Any]) -> InputProcessResult:
        try:
            created = await self.api_repo.create_integration(payload)
        except Exception as exc:
            return InputProcessResult(success=False, error_text=self._integration_error_text(exc))

        integration_id = self._safe_int(created.get("id"), default=-1)
        if integration_id < 0:
            return InputProcessResult(success=True, next_route=ROUTE_INTEGRATIONS, next_payload={})

        job_id: int | None = None
        try:
            job_id = await self.refresh_integration(integration_id)
        except Exception:
            job_id = None

        return InputProcessResult(
            success=True,
            next_route=ROUTE_INTEGRATION_DETAIL,
            next_payload={"id": integration_id} | ({"job_id": job_id} if job_id is not None else {}),
        )

    @staticmethod
    def _build_waiting_input(
        *,
        kind: str,
        draft: dict[str, Any],
        return_route: str,
        return_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "return_route": return_route,
            "return_payload": dict(return_payload),
            "draft": dict(draft),
        }

    @staticmethod
    def _integration_error_text(exc: Exception) -> str:
        text = str(exc).strip()
        if not text:
            return "Integration request failed."
        if "400" in text:
            return "Integration validation failed. Check exchange code, account reference, wallet address and chain."
        if "401" in text or "403" in text:
            return "Integration request is not authorized for this bot."
        if "409" in text:
            return "This integration already exists."
        return text

    @staticmethod
    def _looks_like_evm_address(value: str) -> bool:
        return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", value.strip()))

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _select_exchange_rows(
        exchanges: dict[str, dict[str, Any]],
        hide_small: bool,
    ) -> tuple[list[str], list[str]]:
        rows: list[tuple[str, float]] = []
        for name, acc in sorted(
            exchanges.items(),
            key=lambda item: item[1].get("total_usd", 0),
            reverse=True,
        ):
            total = float(acc.get("total_usd", 0) or 0)
            if hide_small and total < settings.hide_small_balance_threshold:
                continue
            rows.append((name, total))

        labels = [name for name, _ in rows]
        names = [name for name, _ in rows]
        return labels, names

    @staticmethod
    def _wallet_service_name(identifier: str) -> str:
        return f"okx_wallet_{(identifier or '').strip().lower()}"

    @staticmethod
    def _base_service_name(service: str) -> str:
        return str(service).split("#", 1)[0]

    @staticmethod
    def _integration_id_from_service_key(service: str) -> int | None:
        raw = str(service)
        if "#" not in raw:
            return None
        _, suffix = raw.rsplit("#", 1)
        try:
            return int(suffix)
        except ValueError:
            return None

    def _select_wallet_rows(
        self,
        wallets: dict[str, dict[str, Any]],
        hide_small: bool,
        name_map: dict[str, str] | None = None,
    ) -> list[str]:
        rows: list[tuple[str, float]] = []
        for service, acc in sorted(
            wallets.items(),
            key=lambda item: item[1].get("total_usd", 0),
            reverse=True,
        ):
            total = float(acc.get("total_usd", 0) or 0)
            if hide_small and total < settings.hide_small_balance_threshold:
                continue
            display = (name_map or {}).get(service) or self._display_name_for_service(service, {"dex_wallets": wallets}, include_total=False)
            rows.append((display, total))
        return [label for label, _ in rows]

    def _wallet_service_order(
        self,
        wallets: dict[str, dict[str, Any]],
        hide_small: bool,
    ) -> list[str]:
        ordered: list[str] = []
        for service, acc in sorted(
            wallets.items(),
            key=lambda item: item[1].get("total_usd", 0),
            reverse=True,
        ):
            total = float(acc.get("total_usd", 0) or 0)
            if hide_small and total < settings.hide_small_balance_threshold:
                continue
            ordered.append(service)
        return ordered

    def _transaction_source_rows(
        self,
        parsed: dict[str, Any],
        integrations: list[dict[str, Any]],
        name_map: dict[str, str] | None = None,
    ) -> list[str]:
        sources: dict[str, tuple[str, float]] = {}
        spot = parsed.get("spot_exchanges") or {}
        dex = parsed.get("dex_wallets") or {}
        for service, acc in spot.items():
            sources[service] = (self._display_name_for_service(service, parsed, include_total=False), float(acc.get("total_usd", 0) or 0))
        for service, acc in dex.items():
            display = (name_map or {}).get(service) or self._display_name_for_service(service, parsed, include_total=False)
            sources[service] = (display, float(acc.get("total_usd", 0) or 0))
        for item in integrations:
            if not isinstance(item, dict) or not bool(item.get("is_active")):
                continue
            service = self._service_name_from_integration(item)
            if not service or service in sources:
                continue
            sources[service] = (self._integration_label(item), 0.0)
        ordered = sorted(sources.items(), key=lambda item: (item[1][1], item[1][0]), reverse=True)
        return [label for _, (label, _) in ordered]

    def _transaction_source_services(
        self,
        parsed: dict[str, Any],
        integrations: list[dict[str, Any]],
    ) -> list[str]:
        sources: dict[str, tuple[str, float]] = {}
        spot = parsed.get("spot_exchanges") or {}
        dex = parsed.get("dex_wallets") or {}
        for service, acc in spot.items():
            sources[service] = (self._display_name_for_service(service, parsed, include_total=False), float(acc.get("total_usd", 0) or 0))
        for service, acc in dex.items():
            sources[service] = (self._display_name_for_service(service, parsed, include_total=False), float(acc.get("total_usd", 0) or 0))
        for item in integrations:
            if not isinstance(item, dict) or not bool(item.get("is_active")):
                continue
            service = self._service_name_from_integration(item)
            if not service or service in sources:
                continue
            sources[service] = (self._integration_label(item), 0.0)
        ordered = sorted(sources.items(), key=lambda item: (item[1][1], item[1][0]), reverse=True)
        return [service for service, _ in ordered]

    def _display_name_for_service(
        self,
        service: str,
        parsed: dict[str, Any],
        *,
        include_total: bool = True,
    ) -> str:
        if service in (parsed.get("spot_exchanges") or {}):
            total = float((parsed.get("spot_exchanges") or {}).get(service, {}).get("total_usd", 0) or 0)
            return f"{service}{': ' + msg.format_usd(total) if include_total else ''}"
        if service in (parsed.get("dex_wallets") or {}):
            total = float((parsed.get("dex_wallets") or {}).get(service, {}).get("total_usd", 0) or 0)
            short = service.removeprefix("okx_wallet_")
            label = f"Wallet {short}"
            return f"{label}{': ' + msg.format_usd(total) if include_total else ''}"
        return service

    def _service_name_from_integration(self, item: dict[str, Any]) -> str:
        kind = str(item.get("kind") or "").strip().lower()
        if kind == "cex":
            return str(item.get("exchange_code") or "").strip().lower()
        wallet_address = str(item.get("wallet_address") or "").strip().lower()
        if wallet_address:
            return self._wallet_service_name(wallet_address)
        return ""

    def _build_service_name_map(self, integrations: list[dict[str, Any]]) -> dict[str, str]:
        """Map service key → integration.name for integrations that have a custom name set."""
        result: dict[str, str] = {}
        for item in integrations:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            service = self._service_name_from_integration(item)
            if service:
                result[service] = name
        return result

    @staticmethod
    def _integration_label(item: dict[str, Any]) -> str:
        name = str(item.get("name") or "").strip()
        if name:
            return name
        service = str(item.get("exchange_code") or item.get("wallet_address") or item.get("provider") or "source").strip()
        return service

    @staticmethod
    def _locale_from_settings(user_settings: dict[str, Any] | None) -> str:
        if not isinstance(user_settings, dict):
            return "ru"
        return normalize_locale(user_settings.get("language"))

    async def _locale_for_user_id(self, user_id: int | None) -> str:
        if user_id is None:
            return "ru"
        user_settings = await self.user_repo.get_settings(user_id)
        return self._locale_from_settings(user_settings)

    @staticmethod
    def _extract_capabilities(payload: dict[str, Any]) -> dict[str, Any]:
        for key in ("capabilities", "entitlements", "limits", "plan"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _refresh_allowed(capabilities: dict[str, Any]) -> bool:
        if not capabilities:
            return True
        for key in ("refresh", "can_refresh", "balances_refresh", "allow_refresh"):
            value = capabilities.get(key)
            if isinstance(value, bool):
                return value
        return True

    @staticmethod
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

    @staticmethod
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
