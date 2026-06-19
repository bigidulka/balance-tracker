"""Screen orchestration service for bot UI rendering."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram.types import InlineKeyboardButton
from aiogram.utils.formatting import Bold, Text
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import messages as msg
from bot.config import settings
from bot.contracts.callbacks import (
    ACTION_BACK,
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
    pack_callback,
)
from bot.contracts.exchanges import (
    SUPPORTED_CEX_EXCHANGE_CODES,
    SUPPORTED_CEX_EXCHANGE_LABELS,
)
from bot.naming import (
    build_service_name_map as _build_name_map,
    button_label as _btn_label,
    cex_name as _cex_name,
    dex_name as _dex_name,
    default_creation_name as _default_name,
    integration_label as _integration_label,
    service_label as _service_label,
    short_addr as _short_addr,
)
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
    wallet_used: int
    wallet_limit: int
    refresh_interval_seconds: int
    can_refresh: bool
    retry_after_seconds: int
    allow_dex: bool
    last_refresh_at: datetime | None = None

    @property
    def evm_used(self) -> int:
        """Legacy alias for wallet_used."""
        return self.wallet_used

    @property
    def evm_limit(self) -> int:
        """Legacy alias for wallet_limit."""
        return self.wallet_limit


class ScreenService:
    def __init__(self, api_repo: ApiRepository, user_repo: UserRepository) -> None:
        self.api_repo = api_repo
        self.user_repo = user_repo

    async def ensure_access(self, user_id: int) -> str | None:
        if not await self.user_repo.is_local_allowed(user_id):
            return msg.ACCESS_DENIED
        # Do not probe balances on every callback; rely on real screen/API calls.
        # Only auth failures on actual fetches should clear backend session.
        return None

    async def menu_context(
        self,
    ) -> tuple[bool, str | None, dict[str, Any], dict[str, Any]]:
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

    async def render(
        self, *, route: str, payload: dict[str, Any], user_id: int, rev: int
    ) -> RenderedScreen:
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
                title_suffix_key="spot",
                description_key="spot_detail_description",
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
                title_suffix_key="futures",
                description_key="futures_detail_description",
                exchange_key="futures_exchanges",
                exchange=str(payload.get("exchange") or ""),
                exchange_index=self._safe_int(payload.get("i"), default=-1),
                rev=rev,
                user_id=user_id,
            )
        if route == ROUTE_DEX:
            page = self._safe_int(payload.get("page"), default=0)
            wallet_index = self._safe_int(payload.get("i"), default=-1)
            return await self._render_dex(
                page=page, wallet_index=wallet_index, user_id=user_id, rev=rev
            )
        if route == ROUTE_TRANSACTIONS:
            return await self._render_transactions_stub(user_id=user_id, rev=rev)
        if route == ROUTE_NOTIFICATIONS:
            return await self._render_notifications_stub(user_id=user_id, rev=rev)
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
            return await self._render_integration_exchange_picker(
                user_id=user_id, rev=rev
            )
        if route == ROUTE_INTEGRATION_WALLET_PICKER:
            return await self._render_integration_wallet_picker(
                user_id=user_id, rev=rev
            )
        if route == ROUTE_PLAN:
            return await self._render_plan(
                user_id=user_id, rev=rev, reason=str(payload.get("reason") or "")
            )
        if route == ROUTE_PAYMENTS:
            invoice_id = self._safe_int(payload.get("id"), default=-1)
            return await self._render_payments(
                user_id=user_id,
                rev=rev,
                invoice_id=invoice_id if invoice_id > 0 else None,
            )
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
                organization_id=self._safe_int(
                    payload.get("organization_id"), default=0
                ),
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

    async def refresh_main(self) -> dict[str, Any]:
        payload = await self.api_repo.refresh_balances()
        # If refresh is queued, backend data will update asynchronously; do not
        # block the UI by forcing an immediate refetch of the same stale state.
        if bool(payload.get("queued")):
            return payload
        await self.api_repo.get_balances(force_update_cache=True)
        return payload

    async def refresh_transactions(self, *, user_id: int) -> None:
        user_settings = await self.user_repo.get_settings(user_id)
        since_hours = int(user_settings.get("tx_since_hours", 24) or 24)
        await self.api_repo.refresh_transactions(since_hours=since_hours)

    async def apply_transactions_filter(
        self, *, user_id: int, field: str, value: str
    ) -> None:
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

    async def update_display_currency(self, *, user_id: int, currency: str) -> None:
        user_settings = await self.user_repo.get_settings(user_id)
        user_settings["display_currency"] = str(currency).strip().upper()
        await self.user_repo.save_settings(user_id, user_settings)

    async def cycle_display_currency(self, *, user_id: int) -> None:
        """Cycle to the next currency in the supported list."""
        from bot.services.fx_rates import currency_codes
        codes = currency_codes()
        user_settings = await self.user_repo.get_settings(user_id)
        current = str(user_settings.get("display_currency") or "USD").strip().upper()
        try:
            idx = codes.index(current)
            next_code = codes[(idx + 1) % len(codes)]
        except ValueError:
            next_code = "USD"
        user_settings["display_currency"] = next_code
        await self.user_repo.save_settings(user_id, user_settings)


    async def apply_input_value(
        self, *, user_id: int, kind: str, raw_value: str
    ) -> tuple[bool, str]:
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
        elif normalized_kind in {
            "integration_dex_name",
            "integration_dex_wallet_address",
        }:
            waiting["draft"] = {"provider": "okx_wallet", "kind": "dex"}
        elif normalized_kind == "integration_rename":
            integration_id = self._safe_int(
                (return_payload or {}).get("id"), default=-1
            )
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

    def start_wallet_address_flow(
        self,
        *,
        wallet_provider: str,
        return_route: str,
        return_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "kind": "integration_dex_wallet_address",
            "return_route": return_route,
            "return_payload": dict(return_payload or {}),
            "draft": {
                "provider": wallet_provider or "okx_wallet",
                "kind": "dex",
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
            ok, reason = await self.apply_input_value(
                user_id=user_id, kind=kind, raw_value=value
            )
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
        return await self.api_repo.create_payment_invoice(
            amount_usd, invoice_type="balance_topup"
        )

    async def create_plan_invoice(
        self, plan_code: str, amount_usd: float | None = None
    ) -> dict[str, Any]:
        return await self.api_repo.create_payment_invoice(
            amount_usd,
            invoice_type="plan_purchase",
            plan_code=plan_code,
        )

    async def switch_plan(self, plan_code: str) -> dict[str, Any]:
        return await self.api_repo.switch_billing_plan(plan_code)

    async def refresh_payment_invoice(self, invoice_id: int) -> dict[str, Any]:
        return await self.api_repo.refresh_payment_invoice(invoice_id)

    async def render_input_waiting(
        self, *, kind: str, user_id: int, rev: int, error_text: str | None = None
    ) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        return RenderedScreen(
            route="input",
            payload={"kind": kind},
            text=msg.input_waiting_text(kind, error_text=error_text, locale=locale),
            keyboard=kb.input_waiting(rev=rev, locale=locale),
        )

    # ── Billing cache (rarely changes, avoid per-render API calls) ──
    _billing_cache: dict[str, Any] = {}
    _billing_cache_ts: float = 0.0
    _BILLING_CACHE_TTL: float = 60.0  # seconds

    async def _load_billing_payload(self) -> dict[str, Any]:
        import time
        now = time.monotonic()
        if now - self._billing_cache_ts < self._BILLING_CACHE_TTL and self._billing_cache:
            return self._billing_cache
        try:
            payload = await self.api_repo.get_billing_current()
            if payload:
                self._billing_cache = payload
                self._billing_cache_ts = now
                return payload
        except Exception:
            pass
        try:
            fallback = await self.api_repo.get_capabilities()
            if fallback and isinstance(fallback, dict):
                self._billing_cache = fallback
                self._billing_cache_ts = now
                return fallback
        except Exception:
            pass
        return self._billing_cache  # return stale cache on error

    @staticmethod
    def _count_usage(integrations: list[dict[str, Any]]) -> tuple[int, int]:
        cex = 0
        wallets = 0
        for item in integrations:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("is_active", True)):
                continue
            kind = str(item.get("kind") or "").strip().lower()
            if kind == "cex":
                cex += 1
                continue
            if kind == "dex":
                wallets += 1
        return cex, wallets

    @staticmethod
    def _refresh_state(
        last_refresh_at: datetime | None, refresh_interval_seconds: int
    ) -> tuple[bool, int]:
        if refresh_interval_seconds <= 0:
            return True, 0
        normalized = ScreenService._coerce_datetime(last_refresh_at)
        if normalized is None:
            return True, 0
        elapsed_seconds = int(
            (
                datetime.now(timezone.utc) - normalized.astimezone(timezone.utc)
            ).total_seconds()
        )
        if elapsed_seconds >= refresh_interval_seconds:
            return True, 0
        return False, max(refresh_interval_seconds - elapsed_seconds, 1)

    async def _load_tariff_snapshot(self, user_id: int | None) -> TariffSnapshot:
        billing = await self._load_billing_payload()
        plan_payload = (
            billing.get("plan") if isinstance(billing.get("plan"), dict) else {}
        )
        plan_code = (
            str(plan_payload.get("code") or billing.get("plan_code") or "free")
            .strip()
            .lower()
        )
        if plan_code == "full":
            plan_code = "pro"
        tariff = get_tariff_plan(plan_code)
        policy = (
            billing.get("policy") if isinstance(billing.get("policy"), dict) else {}
        )
        limits = (
            billing.get("limits") if isinstance(billing.get("limits"), dict) else {}
        )
        usage = billing.get("usage") if isinstance(billing.get("usage"), dict) else {}
        features = (
            policy.get("features") if isinstance(policy.get("features"), dict) else {}
        )
        capabilities = (
            billing.get("capabilities")
            if isinstance(billing.get("capabilities"), dict)
            else {}
        )

        cex_state = usage.get("cex") if isinstance(usage.get("cex"), dict) else {}
        wallet_state = (
            usage.get("wallets")
            if isinstance(usage.get("wallets"), dict)
            else usage.get("evm")
            if isinstance(usage.get("evm"), dict)
            else {}
        )
        cex_used = self._safe_int(cex_state.get("active"), default=-1)
        wallet_used = self._safe_int(wallet_state.get("active"), default=-1)
        cex_limit = self._safe_int(limits.get("max_cex_accounts"), default=0)
        wallet_limit = self._safe_int(
            limits.get("max_wallets") or limits.get("max_evm_wallets"), default=0
        )
        refresh_interval_seconds = self._safe_int(
            (
                billing.get("background")
                if isinstance(billing.get("background"), dict)
                else {}
            ).get("refresh_interval_seconds"),
            default=0,
        )

        if cex_used < 0 or wallet_used < 0:
            try:
                integrations = await self.api_repo.get_integrations()
            except Exception:
                integrations = []
            fallback_cex_used, fallback_wallet_used = self._count_usage(integrations)
            if cex_used < 0:
                cex_used = fallback_cex_used
            if wallet_used < 0:
                wallet_used = fallback_wallet_used
        if cex_limit <= 0:
            cex_limit = tariff.max_cex_accounts
        if wallet_limit < 0:
            wallet_limit = 0
        if (
            wallet_limit == 0
            and "max_wallets" not in limits
            and "max_evm_wallets" not in limits
        ):
            wallet_limit = tariff.max_wallets
        if refresh_interval_seconds <= 0:
            refresh_interval_seconds = tariff.refresh_interval_seconds

        allow_dex_raw = capabilities.get("allow_dex")
        if isinstance(allow_dex_raw, bool):
            allow_dex = allow_dex_raw
        else:
            features_allow_dex = features.get("allow_dex")
            allow_dex = (
                bool(features_allow_dex)
                if isinstance(features_allow_dex, bool)
                else self._allow_dex(capabilities)
            )
        last_refresh_at = self._coerce_datetime(billing.get("last_refresh_at"))
        refresh_from_capabilities = capabilities.get(
            "can_refresh", capabilities.get("refresh")
        )
        retry_after_seconds = self._safe_int(
            (
                billing.get("throttling")
                if isinstance(billing.get("throttling"), dict)
                else {}
            ).get("retry_after_seconds"),
            default=0,
        )
        if isinstance(refresh_from_capabilities, bool):
            can_refresh = refresh_from_capabilities
        else:
            can_refresh, retry_after_seconds = self._refresh_state(
                last_refresh_at, refresh_interval_seconds
            )
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
            wallet_used=wallet_used,
            wallet_limit=wallet_limit,
            refresh_interval_seconds=refresh_interval_seconds,
            can_refresh=can_refresh,
            retry_after_seconds=retry_after_seconds,
            allow_dex=allow_dex,
            last_refresh_at=last_refresh_at,
        )

    async def _render_plan(
        self, *, user_id: int, rev: int, reason: str = ""
    ) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        snapshot = await self._load_tariff_snapshot(user_id)
        billing = await self._load_billing_payload()
        wallet = (
            billing.get("wallet") if isinstance(billing.get("wallet"), dict) else {}
        )
        try:
            plans_payload = await self.api_repo.get_billing_plans()
        except Exception:
            plans_payload = {}
        plans = (
            plans_payload.get("plans")
            if isinstance(plans_payload.get("plans"), list)
            else []
        )
        reason_text = reason.strip()
        if reason_text == "cex_limit_reached":
            reason_text = f"{t(locale, 'cex_accounts')}: {snapshot.cex_used}/{snapshot.cex_limit} {t(locale, 'limit_reached')}"
        elif reason_text in ("evm_limit_reached", "wallet_limit_reached"):
            reason_text = f"{t(locale, 'wallets')}: {snapshot.wallet_used}/{snapshot.wallet_limit} {t(locale, 'limit_reached')}"

        text = msg.plan_text(
            plan_code=snapshot.plan_code,
            plan_name=snapshot.plan_name,
            cex_used=snapshot.cex_used,
            cex_limit=snapshot.cex_limit,
            evm_used=snapshot.wallet_used,
            evm_limit=snapshot.wallet_limit,
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

    async def _render_payments(
        self, *, user_id: int, rev: int, invoice_id: int | None
    ) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        balance_payload = await self.api_repo.get_billing_balance()
        invoices_payload = await self.api_repo.list_payment_invoices()
        invoices = (
            invoices_payload.get("items")
            if isinstance(invoices_payload.get("items"), list)
            else []
        )
        active_invoice = next(
            (
                item
                for item in invoices
                if isinstance(item, dict)
                and (invoice_id is None or int(item.get("id") or 0) == invoice_id)
            ),
            None,
        )
        latest_invoice_dt = max(
            (
                self._coerce_datetime(item.get("created_at"))
                for item in invoices
                if isinstance(item, dict)
                and self._coerce_datetime(item.get("created_at")) is not None
            ),
            default=None,
        )
        return RenderedScreen(
            route=ROUTE_PAYMENTS,
            payload={"id": int(active_invoice.get("id"))}
            if isinstance(active_invoice, dict) and active_invoice.get("id")
            else {},
            text=msg.payments_text(
                balance_usd=float(balance_payload.get("available") or 0.0),
                invoices=[item for item in invoices if isinstance(item, dict)],
                active_invoice=active_invoice
                if isinstance(active_invoice, dict)
                else None,
                locale=locale,
            ),
            keyboard=kb.payments_screen(
                invoice_id=int(active_invoice.get("id"))
                if isinstance(active_invoice, dict) and active_invoice.get("id")
                else None,
                invoice_url=(
                    str(active_invoice.get("bot_invoice_url") or active_invoice.get("pay_url") or "")
                    if isinstance(active_invoice, dict)
                    else None
                ),
                rev=rev,
                locale=locale,
                refresh_time_label=self._refresh_time_label(
                    dt=latest_invoice_dt, locale=locale
                ),
            ),
        )

    async def _render_admin(self, *, user_id: int, rev: int) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        if not await self.is_admin_user(user_id):
            return RenderedScreen(
                route=ROUTE_ADMIN,
                payload={},
                text=msg.gated_feature_text("Admin access required.", locale=locale),
                keyboard=kb.admin_screen(rev=rev, locale=locale),
            )
        return RenderedScreen(
            route=ROUTE_ADMIN,
            payload={},
            text=msg.admin_panel_text(locale=locale),
            keyboard=kb.admin_screen(rev=rev, locale=locale),
        )

    async def _render_admin_users(
        self, *, user_id: int, rev: int, page: int
    ) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        if not await self.is_admin_user(user_id):
            return RenderedScreen(
                route=ROUTE_ADMIN_USERS,
                payload={"page": 0},
                text=msg.gated_feature_text("Admin access required.", locale=locale),
                keyboard=kb.admin_screen(rev=rev, locale=locale),
            )
        page = max(0, page)
        limit = 8
        payload = await self.api_repo.admin_list_users(limit=limit, offset=page * limit)
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        total = self._safe_int(payload.get("total"), default=len(items))
        return RenderedScreen(
            route=ROUTE_ADMIN_USERS,
            payload={"page": page},
            text=msg.admin_users_text(
                items=[item for item in items if isinstance(item, dict)],
                total=total,
                page=page,
                page_size=limit,
                locale=locale,
            ),
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
            return RenderedScreen(
                route=ROUTE_ADMIN_USER_DETAIL,
                payload={},
                text=msg.gated_feature_text("Admin access required.", locale=locale),
                keyboard=kb.admin_screen(rev=rev, locale=locale),
            )
        payload = await self.api_repo.admin_get_user_detail(
            user_id=target_user_id,
            organization_id=organization_id,
        )
        return RenderedScreen(
            route=ROUTE_ADMIN_USER_DETAIL,
            payload={"user_id": target_user_id, "organization_id": organization_id},
            text=msg.admin_user_detail_text(
                payload if isinstance(payload, dict) else {}, locale=locale
            ),
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
            return (
                False,
                f"{t(locale, 'cex_accounts')}: {snapshot.cex_used}/{snapshot.cex_limit} {t(locale, 'limit_reached')}",
            )
        return True, None

    # Chains counted against the max_evm_wallets / max_wallets plan limit.
    # Non-EVM chains (Solana, TRON, TON, SUI) are exempt from this limit.
    _EVM_CHAINS: frozenset[str] = frozenset(
        {
            "ethereum",
            "arbitrum",
            "optimism",
            "base",
            "bsc",
            "polygon",
            "avalanche",
            "fantom",
            "gnosis",
            "zksync",
            "linea",
            "scroll",
            "mantle",
            "blast",
            "taiko",
        }
    )

    async def can_add_evm_wallet(
        self, *, user_id: int, chain: str
    ) -> tuple[bool, str | None]:
        locale = await self._locale_for_user_id(user_id)
        # Non-EVM chains (solana, tron, ton, sui…) are not counted against
        # the max_evm_wallets limit — always allowed from a quota perspective.
        if chain.lower() not in self._EVM_CHAINS:
            return True, None
        snapshot = await self._load_tariff_snapshot(user_id)
        if snapshot.wallet_limit > 0 and snapshot.wallet_used >= snapshot.wallet_limit:
            return (
                False,
                f"{t(locale, 'wallets')}: {snapshot.wallet_used}/{snapshot.wallet_limit} {t(locale, 'limit_reached')}",
            )
        return True, None

    def _refresh_time_label(
        self,
        *,
        dt: datetime | str | None = None,
        locale: str = "ru",
        fallback_to_now: bool = False,
        stale_after_seconds: int | None = None,
        force_stale: bool = False,
    ) -> str | None:
        resolved = self._coerce_datetime(dt)
        if resolved is None and fallback_to_now:
            resolved = datetime.now(timezone.utc)
        if resolved is None:
            return None
        label = self._format_relative_elapsed(resolved, locale=locale)
        if force_stale or (
            stale_after_seconds and self._is_stale(resolved, stale_after_seconds)
        ):
            return f"⚠️ {label}"
        return label

    @staticmethod
    def _is_stale(dt: datetime, stale_after_seconds: int) -> bool:
        if stale_after_seconds <= 0:
            return False
        now = datetime.now(timezone.utc)
        age_seconds = max(0, int((now - dt.astimezone(timezone.utc)).total_seconds()))
        return age_seconds > stale_after_seconds

    @staticmethod
    def _stale_after_seconds(refresh_interval_seconds: int) -> int | None:
        interval = max(int(refresh_interval_seconds or 0), 0)
        if interval <= 0:
            return None
        return interval * 3

    @staticmethod
    def _coerce_datetime(value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return (
                value
                if value.tzinfo is not None
                else value.replace(tzinfo=timezone.utc)
            )
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            return (
                parsed
                if parsed.tzinfo is not None
                else parsed.replace(tzinfo=timezone.utc)
            )
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

    def _has_stale_services(
        self,
        data: dict[str, Any],
        *,
        service_names: set[str] | None = None,
    ) -> bool:
        services = data.get("services", [])
        if not isinstance(services, list):
            return False
        normalized_names = {name.strip().lower() for name in (service_names or set()) if name}
        for svc in services:
            if not isinstance(svc, dict):
                continue
            service_name = str(svc.get("service") or "").strip().lower()
            if normalized_names and service_name not in normalized_names:
                continue
            if not bool(svc.get("actual", False)):
                return True
        return False

    def _latest_exchange_update(
        self, data: dict[str, Any], exchange: str
    ) -> datetime | None:
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
        import asyncio as _asyncio

        async def _safe_menu_context() -> tuple[bool, str, dict[str, Any], dict[str, Any]]:
            try:
                return await _asyncio.wait_for(self.menu_context(), timeout=8)
            except Exception:
                return True, "-", {"can_refresh": True}, {}

        async def _safe_dashboard_summary() -> dict[str, Any] | None:
            try:
                return await _asyncio.wait_for(
                    self.api_repo.get_dashboard_summary(),
                    timeout=12,
                )
            except Exception:
                return None

        menu_coro = _safe_menu_context()
        summary_coro = _safe_dashboard_summary()
        try:
            (allow_dex, plan_label, capabilities, throttling), summary = await _asyncio.gather(
                menu_coro, summary_coro
            )
        except Exception:
            allow_dex, plan_label, capabilities, throttling = True, "-", {"can_refresh": True}, {}
            summary = None

        can_refresh = self._refresh_allowed(capabilities)
        retry_after = int(throttling.get("retry_after_seconds") or 0)
        refresh_interval_seconds = int(throttling.get("min_refresh_interval_seconds") or 0)
        stale_after_seconds = self._stale_after_seconds(refresh_interval_seconds)
        locale = await self._locale_for_user_id(user_id)
        refresh_time_label: str | None = None

        # Currency conversion for dashboard
        user_settings = await self.user_repo.get_settings(user_id)
        display_currency = str(user_settings.get("display_currency") or "USD").strip().upper()
        converted_total: float | None = None
        currency_label = ""
        if display_currency != "USD" and summary:
            try:
                from bot.services.fx_rates import fetch_fx_rates, convert_usd, format_fiat as fx_format
                rates = await _asyncio.wait_for(fetch_fx_rates(), timeout=2)
                total_usd = float(summary.get("total_usd") or 0.0)
                converted_total = convert_usd(total_usd, display_currency, rates)
                currency_label = fx_format(converted_total, display_currency)
            except Exception:
                converted_total = None
                currency_label = ""

        try:
            if summary is None:
                summary = await _safe_dashboard_summary()
            if summary is None:
                raise TimeoutError("dashboard summary unavailable")
            if settings.no_backend_ui_mode:
                summary["plan"] = summary.get("plan") or {
                    "name": "UI Preview",
                    "code": "ui_preview",
                }
                summary["freshness"] = summary.get("freshness") or "mock_preview_mode"
            refresh_time_label = self._refresh_time_label(
                dt=summary.get("latest_updated_at"),
                locale=locale,
                stale_after_seconds=stale_after_seconds,
            )
            text = msg.dashboard_text(
                summary,
                locale=locale,
                currency_label=currency_label,
            )
        except Exception:
            # Do not call get_balances() here: if dashboard summary is slow,
            # balances endpoint is usually slow too and message handling times out.
            fallback_summary = {
                "total_usd": 0.0,
                "exchanges_count": 0,
                "spot_total": 0.0,
                "futures_total": 0.0,
                "dex_total": 0.0,
                "plan": {"name": plan_label or "-", "code": "unknown"},
                "capabilities": capabilities,
                "throttling": throttling,
                "integrations": {"total": 0, "active": 0},
                "transactions_24h": {"total": 0, "pending": 0, "failed": 0},
                "freshness": "temporarily unavailable",
            }
            text = msg.dashboard_text(
                fallback_summary,
                locale=locale,
                currency_label="",
            )

        keyboard = kb.main_menu(
            rev=rev,
            locale=locale,
            allow_dex=allow_dex,
            plan_label=plan_label,
            can_refresh=can_refresh,
            retry_after_seconds=retry_after,
            refresh_time_label=refresh_time_label,
        )
        return RenderedScreen(
            route=ROUTE_MAIN, payload={}, text=text, keyboard=keyboard
        )

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
        snapshot = await self._load_tariff_snapshot(user_id)
        refresh_time_label = self._refresh_time_label(
            dt=self._latest_balance_update(data),
            locale=locale,
            stale_after_seconds=self._stale_after_seconds(snapshot.refresh_interval_seconds),
            force_stale=self._has_stale_services(data, service_names=set((parsed.get(exchange_key) or {}).keys())),
        )

        try:
            integrations = await self.api_repo.get_integrations()
        except Exception:
            integrations = []
        name_map = _build_name_map(integrations)
        stale_services = {
            str(svc.get("service") or "").strip().lower()
            for svc in (data.get("services") or [])
            if isinstance(svc, dict) and not bool(svc.get("actual", False))
        }

        lines, exchanges = self._select_exchange_rows(
            parsed.get(exchange_key, {}),
            hide_small=bool(user_settings.get("hide_small")),
            name_map=name_map,
            stale_services=stale_services,
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
            title=t(locale, "spot_balances")
            if route == ROUTE_SPOT_LIST
            else t(locale, "futures_balances"),
            total_usd=float(parsed.get(total_key, 0) or 0),
            description=t(locale, "spot_description")
            if route == ROUTE_SPOT_LIST
            else t(locale, "futures_description"),
            lines=page_lines,
            visible_count=len(page_lines),
            total_count=total_count,
            page=page,
            total_pages=total_pages,
            locale=locale,
        )
        keyboard = (
            kb.spot_exchanges(
                exchanges,
                page=page,
                per_page=per_page,
                rev=rev,
                locale=locale,
                labels=lines,
                refresh_time_label=refresh_time_label,
            )
            if route == ROUTE_SPOT_LIST
            else kb.futures_exchanges(
                exchanges,
                page=page,
                per_page=per_page,
                rev=rev,
                locale=locale,
                labels=lines,
                refresh_time_label=refresh_time_label,
            )
        )
        return RenderedScreen(
            route=route, payload={"page": page}, text=text, keyboard=keyboard
        )

    async def _render_exchange_detail(
        self,
        *,
        route: str,
        back_route: str,
        title_suffix_key: str,
        description_key: str,
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
        snapshot = await self._load_tariff_snapshot(user_id)
        refresh_time_label = self._refresh_time_label(
            dt=self._latest_exchange_update(data, exchange),
            locale=locale,
            stale_after_seconds=self._stale_after_seconds(snapshot.refresh_interval_seconds),
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
                or float(acc_row.get("total_usd", 0) or 0)
                >= settings.hide_small_balance_threshold
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

        try:
            integrations = await self.api_repo.get_integrations()
        except Exception:
            integrations = []
        detail_name_map = _build_name_map(integrations)
        stale_services = {
            str(svc.get("service") or "").strip().lower()
            for svc in (data.get("services") or [])
            if isinstance(svc, dict) and not bool(svc.get("actual", False))
        }
        exchange_display = detail_name_map.get(exchange) or _service_label(exchange)
        if exchange.strip().lower() in stale_services:
            exchange_display = f"{exchange_display} ⚠️"

        title_suffix = t(locale, title_suffix_key)
        text = msg.exchange_detail_text(
            title=f"{exchange_display} • {title_suffix}",
            total_usd=float(acc.get("total_usd", 0) or 0),
            description=t(locale, description_key),
            assets=shown_assets,
            has_more=has_more,
            more_count=more_count,
            locale=locale,
        )
        return RenderedScreen(
            route=route,
            payload={"exchange": exchange},
            text=text,
            keyboard=kb.exchange_detail(
                back_route,
                rev=rev,
                locale=locale,
                refresh_time_label=refresh_time_label,
            ),
        )

    async def _render_dex(
        self, *, page: int, wallet_index: int, user_id: int, rev: int
    ) -> RenderedScreen:
        data = await self.api_repo.get_balances()
        parsed = parse_balances(data)
        user_settings = await self.user_repo.get_settings(user_id)
        locale = self._locale_from_settings(user_settings)
        snapshot = await self._load_tariff_snapshot(user_id)
        refresh_time_label = self._refresh_time_label(
            dt=self._latest_balance_update(data),
            locale=locale,
            stale_after_seconds=self._stale_after_seconds(snapshot.refresh_interval_seconds),
            force_stale=self._has_stale_services(data, service_names=set((parsed.get("dex_wallets") or {}).keys())),
        )

        try:
            integrations = await self.api_repo.get_integrations()
        except Exception:
            integrations = []
        name_map = self._build_service_name_map(integrations)
        stale_services = {
            str(svc.get("service") or "").strip().lower()
            for svc in (data.get("services") or [])
            if isinstance(svc, dict) and not bool(svc.get("actual", False))
        }

        dex_wallets = parsed.get("dex_wallets") or {}
        wallet_items = self._select_wallet_rows(
            dex_wallets,
            hide_small=bool(user_settings.get("hide_small")),
            name_map=name_map,
            stale_services=stale_services,
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
                display = name_map.get(svc) or self._display_name_for_service(
                    svc, parsed, include_total=False
                )
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
            return RenderedScreen(
                route=ROUTE_DEX, payload={"page": page}, text=text, keyboard=keyboard
            )

        wallet_services = self._wallet_service_order(
            dex_wallets,
            hide_small=bool(user_settings.get("hide_small")),
        )
        if not (0 <= wallet_index < len(wallet_services)):
            return await self._render_dex(
                page=0, wallet_index=-1, user_id=user_id, rev=rev
            )
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
        label = name_map.get(service) or self._display_name_for_service(
            service, parsed, include_total=False
        )
        if service.strip().lower() in stale_services:
            label = f"{label} ⚠️"
        address = self._wallet_address_from_service(service)

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
        portfolio_tokens: list[dict] = (
            portfolio.get("tokens") or [] if portfolio else []
        )
        portfolio_total_tokens: int = (
            int(portfolio.get("totalTokens") or 0) if portfolio else 0
        )
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

    async def _render_transactions_stub(
        self, *, user_id: int, rev: int
    ) -> RenderedScreen:
        """Placeholder screen while Transactions feature is in development."""
        user_settings = await self.user_repo.get_settings(user_id)
        locale = self._locale_from_settings(user_settings)
        text = Text(
            Bold(f"\U0001f6a7 {t(locale, 'transactions')}"),
            "\n\n",
            t(locale, "transactions_under_dev"),
        )
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=t(locale, "back"),
                callback_data=pack_callback(ROUTE_TRANSACTIONS, ACTION_BACK, rev=rev),
            )
        )
        return RenderedScreen(
            route=ROUTE_TRANSACTIONS,
            payload={},
            text=text,
            keyboard=builder.as_markup(),
        )

    async def update_notification_toggle(self, *, field: str) -> dict[str, Any]:
        current = await self.api_repo.get_notification_settings()
        value = not bool(current.get(field))
        return await self.api_repo.update_notification_settings({field: value})

    async def _render_notifications_stub(
        self, *, user_id: int, rev: int
    ) -> RenderedScreen:
        user_settings = await self.user_repo.get_settings(user_id)
        locale = self._locale_from_settings(user_settings)
        settings_payload = await self.api_repo.get_notification_settings()
        events_payload = await self.api_repo.get_notification_events(limit=5)
        events = events_payload.get("events") if isinstance(events_payload, dict) else []
        if not isinstance(events, list):
            events = []

        def _status(name: str) -> str:
            return "✅" if bool(settings_payload.get(name)) else "⬜"

        lines = [
            f"{_status('enabled')} Общие уведомления",
            f"{_status('system_enabled')} Системные события",
            f"{_status('transaction_enabled')} Транзакции CEX (beta)",
            f"{_status('balance_enabled')} Изменения баланса",
        ]
        recent = []
        for item in events[:5]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("event_type") or "event")
            status = str(item.get("status") or "pending")
            recent.append(f"• {title} — {status}")

        text = Text(
            Bold(f"🔔 {t(locale, 'notifications')}"),
            "\n\n",
            "Настройка уведомлений по системе, интеграциям и транзакциям.",
            "\n\n",
            "\n".join(lines),
            "\n\n",
            Bold("Последние события"),
            "\n",
            "\n".join(recent) if recent else "Событий пока нет",
        )
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=f"{_status('enabled')} Все",
                callback_data=pack_callback(
                    ROUTE_NOTIFICATIONS,
                    ACTION_TOGGLE,
                    rev=rev,
                    payload="field=enabled",
                ),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=f"{_status('system_enabled')} Системные",
                callback_data=pack_callback(
                    ROUTE_NOTIFICATIONS,
                    ACTION_TOGGLE,
                    rev=rev,
                    payload="field=system_enabled",
                ),
            ),
            InlineKeyboardButton(
                text=f"{_status('transaction_enabled')} Tx beta",
                callback_data=pack_callback(
                    ROUTE_NOTIFICATIONS,
                    ACTION_TOGGLE,
                    rev=rev,
                    payload="field=transaction_enabled",
                ),
            ),
        )
        builder.row(
            InlineKeyboardButton(
                text=f"{_status('balance_enabled')} Баланс",
                callback_data=pack_callback(
                    ROUTE_NOTIFICATIONS,
                    ACTION_TOGGLE,
                    rev=rev,
                    payload="field=balance_enabled",
                ),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=t(locale, "back"),
                callback_data=pack_callback(ROUTE_NOTIFICATIONS, ACTION_BACK, rev=rev),
            )
        )
        return RenderedScreen(
            route=ROUTE_NOTIFICATIONS,
            payload={},
            text=text,
            keyboard=builder.as_markup(),
        )

    async def _render_transactions(
        self, *, page: int, source_index: int, user_id: int, rev: int
    ) -> RenderedScreen:
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
            source_rows = self._transaction_source_rows(
                parsed, integrations, name_map=name_map
            )
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
                lines=page_rows,
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
            return RenderedScreen(
                route=ROUTE_TRANSACTIONS,
                payload={"page": page},
                text=text,
                keyboard=keyboard,
            )

        balances = await self.api_repo.get_balances()
        parsed = parse_balances(balances)
        integrations = await self.api_repo.get_integrations()
        name_map = self._build_service_name_map(integrations)
        source_services = self._transaction_source_services(parsed, integrations)
        if not (0 <= source_index < len(source_services)):
            return await self._render_transactions(
                page=0, source_index=-1, user_id=user_id, rev=rev
            )
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
            source_label=self._display_name_for_service(
                service, parsed, name_map=name_map
            ),
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
        return RenderedScreen(
            route=ROUTE_TRANSACTIONS,
            payload={"i": source_index, "page": page},
            text=text,
            keyboard=keyboard,
        )

    async def _render_integrations(
        self, *, user_id: int, rev: int, page: int = 0
    ) -> RenderedScreen:
        integrations = await self.api_repo.get_integrations()
        balances: dict[str, Any] = {}
        try:
            balances = await self.api_repo.get_balances()
        except Exception:
            balances = {}
        locale = await self._locale_for_user_id(user_id)
        snapshot = await self._load_tariff_snapshot(user_id)
        per_page = 8
        total = len(integrations)
        total_pages = max(1, math.ceil(total / per_page))
        page = max(0, min(page, total_pages - 1))
        stale_services = {
            str(svc.get("service") or "").strip().lower()
            for svc in (balances.get("services") or [])
            if isinstance(svc, dict) and not bool(svc.get("actual", False))
        }
        # Enrich every item once; text and keyboard must render the same state.
        enriched = []
        for item in integrations:
            service_name = self._service_name_from_integration(item).strip().lower()
            health_status = str(item.get("health_status") or "").strip().lower()
            if service_name in stale_services and health_status in {"", "ok", "unknown"}:
                health_status = "warning"
            enriched.append(
                dict(
                    item,
                    display_name=self._display_name_for_integration(item),
                    health_status=health_status or item.get("health_status"),
                )
            )
        text = msg.integrations_text(
            enriched,
            page=page,
            per_page=per_page,
            cex_used=snapshot.cex_used,
            cex_limit=snapshot.cex_limit,
            evm_used=snapshot.wallet_used,
            evm_limit=snapshot.wallet_limit,
            locale=locale,
        )
        latest_updated_at = max(
            (
                dt
                for dt in (
                    self._coerce_datetime(item.get("updated_at"))
                    for item in integrations
                    if isinstance(item, dict)
                )
                if dt is not None
            ),
            default=None,
        )
        return RenderedScreen(
            route=ROUTE_INTEGRATIONS,
            payload={"page": page},
            text=text,
            keyboard=kb.integrations(
                enriched,
                rev=rev,
                page=page,
                per_page=per_page,
                locale=locale,
                refresh_time_label=self._refresh_time_label(
                    dt=latest_updated_at, locale=locale
                ),
            ),
        )

    async def _render_integration_exchange_picker(
        self, *, user_id: int, rev: int
    ) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        return RenderedScreen(
            route=ROUTE_INTEGRATION_EXCHANGE_PICKER,
            payload={},
            text=msg.integration_exchange_picker_text(locale=locale),
            keyboard=kb.integration_exchange_picker(rev=rev, locale=locale),
        )

    async def _render_integration_wallet_picker(
        self, *, user_id: int, rev: int
    ) -> RenderedScreen:
        locale = await self._locale_for_user_id(user_id)
        return RenderedScreen(
            route=ROUTE_INTEGRATION_WALLET_PICKER,
            payload={},
            text=msg.integration_wallet_picker_text(locale=locale),
            keyboard=kb.integration_wallet_picker(rev=rev, locale=locale),
        )

    async def _render_integration_detail(
        self, *, integration_id: int, job_id: int | None, user_id: int, rev: int
    ) -> RenderedScreen:
        integrations = await self.api_repo.get_integrations()
        item = next(
            (i for i in integrations if int(i.get("id", -1)) == integration_id), None
        )
        if item is None:
            return await self._render_integrations(user_id=user_id, rev=rev)
        locale = await self._locale_for_user_id(user_id)

        text = msg.integration_detail_text(
            dict(item, display_name=self._display_name_for_integration(item)),
            job_id=job_id,
            locale=locale,
        )
        return RenderedScreen(
            route=ROUTE_INTEGRATION_DETAIL,
            payload={"id": integration_id}
            | ({"job_id": job_id} if job_id is not None else {}),
            text=text,
            keyboard=kb.integration_actions(
                integration_id,
                is_active=bool(item.get("is_active")),
                rev=rev,
                locale=locale,
                refresh_time_label=self._refresh_time_label(
                    dt=item.get("updated_at"), locale=locale
                ),
            ),
        )

    async def _render_settings(self, *, user_id: int, rev: int) -> RenderedScreen:
        user_settings = await self.user_repo.get_settings(user_id)
        locale = self._locale_from_settings(user_settings)
        display_currency = str(user_settings.get("display_currency") or "USD").strip().upper()
        text = msg.settings_text(
            hide_small=bool(user_settings.get("hide_small")),
            threshold=settings.hide_small_balance_threshold,
            language=locale,
            display_currency=display_currency,
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
                display_currency=display_currency,
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
        return_payload = (
            waiting.get("return_payload")
            if isinstance(waiting.get("return_payload"), dict)
            else {}
        )
        draft = waiting.get("draft") if isinstance(waiting.get("draft"), dict) else {}
        draft = dict(draft)

        if kind == "integration_cex_account_ref":
            if not raw_value:
                return InputProcessResult(
                    success=False,
                    error_text="Enter account reference, for example main.",
                )
            exchange_code = str(draft.get("exchange_code") or "").strip().lower()
            if exchange_code not in SUPPORTED_CEX_EXCHANGE_CODES:
                return InputProcessResult(
                    success=False, error_text="Choose a supported exchange first."
                )
            draft["account_ref"] = raw_value
            if not draft.get("name"):
                draft["name"] = _default_name(
                    kind="cex",
                    exchange_code=exchange_code,
                    account_ref=raw_value,
                )
            next_kind = (
                "integration_cex_api_token"
                if exchange_code == "cryptobot"
                else "integration_cex_api_key"
            )
            return InputProcessResult(
                success=True,
                next_waiting=self._build_waiting_input(
                    kind=next_kind,
                    draft=draft,
                    return_route=return_route,
                    return_payload=return_payload,
                ),
            )

        if kind == "integration_cex_api_token":
            if not raw_value:
                return InputProcessResult(success=False, error_text="Enter app token.")
            draft["api_token"] = raw_value
            verify_error = await self._verify_cex_credentials(draft)
            if verify_error:
                return InputProcessResult(success=False, error_text=verify_error)
            return await self._create_integration(draft)

        if kind == "integration_cex_api_key":
            if not raw_value:
                return InputProcessResult(success=False, error_text="Enter API key.")
            draft["api_key"] = raw_value
            return InputProcessResult(
                success=True,
                next_waiting=self._build_waiting_input(
                    kind="integration_cex_api_secret",
                    draft=draft,
                    return_route=return_route,
                    return_payload=return_payload,
                ),
            )

        if kind == "integration_cex_api_secret":
            if not raw_value:
                return InputProcessResult(success=False, error_text="Enter API secret.")
            draft["api_secret"] = raw_value
            return InputProcessResult(
                success=True,
                next_waiting=self._build_waiting_input(
                    kind="integration_cex_api_password",
                    draft=draft,
                    return_route=return_route,
                    return_payload=return_payload,
                ),
            )

        if kind == "integration_cex_api_password":
            value = raw_value.strip()
            if value and value != "-":
                draft["api_password"] = value
            verify_error = await self._verify_cex_credentials(draft)
            if verify_error:
                return InputProcessResult(success=False, error_text=verify_error)
            return await self._create_integration(draft)

        if kind == "integration_dex_wallet_address":
            if len(raw_value) < 8:
                return InputProcessResult(
                    success=False, error_text="Enter a full wallet address."
                )
            draft["wallet_address"] = raw_value
            if self._looks_like_sui_address(raw_value):
                draft["chain"] = "sui"
                draft["provider"] = "sui"
                if not draft.get("name"):
                    draft["name"] = _default_name(
                        kind="dex", wallet_address=raw_value, chain="sui"
                    )
                return await self._create_integration(draft)
            if self._looks_like_evm_address(raw_value):
                allowed, reason = await self.can_add_evm_wallet(
                    user_id=user_id, chain="ethereum"
                )
                if not allowed:
                    return InputProcessResult(success=False, error_text=reason)
                draft["chain"] = "ethereum"
                if not draft.get("name"):
                    draft["name"] = _default_name(
                        kind="dex", wallet_address=raw_value, chain="ethereum"
                    )
                return await self._create_integration(draft)
            if self._looks_like_tron_address(raw_value):
                draft["chain"] = "tron"
                draft["provider"] = "tron_ton"
                if not draft.get("name"):
                    draft["name"] = _default_name(
                        kind="dex", wallet_address=raw_value, chain="tron"
                    )
                return await self._create_integration(draft)
            if self._looks_like_ton_address(raw_value):
                draft["chain"] = "ton"
                draft["provider"] = "tron_ton"
                if not draft.get("name"):
                    draft["name"] = _default_name(
                        kind="dex", wallet_address=raw_value, chain="ton"
                    )
                return await self._create_integration(draft)
            if self._looks_like_solana_address(raw_value):
                draft["chain"] = "solana"
                draft["provider"] = "okx_wallet"
                if not draft.get("name"):
                    draft["name"] = _default_name(
                        kind="dex", wallet_address=raw_value, chain="solana"
                    )
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
                return InputProcessResult(
                    success=False,
                    error_text="Use chain like ethereum, arbitrum, bsc, solana, tron or ton.",
                )
            draft["chain"] = value
            if not draft.get("name"):
                address = str(draft.get("wallet_address") or "")
                draft["name"] = _default_name(
                    kind="dex", wallet_address=address, chain=value
                )
            return await self._create_integration(draft)

        if kind == "integration_rename":
            if not raw_value:
                return InputProcessResult(success=False, error_text="Enter a new name.")
            integration_id = self._safe_int(draft.get("id"), default=-1)
            if integration_id < 0:
                return InputProcessResult(
                    success=False, error_text="Integration not found."
                )
            try:
                await self.api_repo.update_integration(integration_id, raw_value)
            except Exception as exc:
                return InputProcessResult(
                    success=False, error_text=self._integration_error_text(exc)
                )
            return InputProcessResult(
                success=True,
                next_route=ROUTE_INTEGRATION_DETAIL,
                next_payload={"id": integration_id},
            )

        return InputProcessResult(
            success=False, error_text="Unknown integration input step."
        )

    async def _verify_cex_credentials(self, draft: dict[str, Any]) -> str | None:
        """Verify CEX API credentials via the API before creating integration.

        Returns an error message string if verification fails, or None on success.
        """
        exchange_code = str(draft.get("exchange_code") or "").strip().lower()
        api_token = str(draft.get("api_token") or "").strip()
        if exchange_code == "cryptobot":
            if not api_token:
                return None
            verify_payload: dict[str, Any] = {
                "exchange_code": exchange_code,
                "api_token": api_token,
            }
        else:
            api_key = str(draft.get("api_key") or "").strip()
            api_secret = str(draft.get("api_secret") or "").strip()
            if not exchange_code or not api_key or not api_secret:
                return None  # skip verification if incomplete — creation will fail anyway

            verify_payload = {
                "exchange_code": exchange_code,
                "api_key": api_key,
                "api_secret": api_secret,
            }
        if draft.get("api_password"):
            verify_payload["api_password"] = draft["api_password"]
        if draft.get("api_uid"):
            verify_payload["api_uid"] = draft["api_uid"]

        try:
            result = await self.api_repo.verify_integration_credentials(verify_payload)
        except Exception:
            # If verification endpoint is unavailable, skip and let creation proceed
            return None

        if isinstance(result, dict) and not result.get("ok", False):
            error = str(result.get("error") or "Invalid credentials")
            # Provide user-friendly messages
            lower = error.lower()
            if exchange_code == "cryptobot":
                return "CryptoBot app token invalid. Check app token in @CryptoBot."
            if "auth" in lower or "key" in lower or "sign" in lower:
                return (
                    "API credentials are invalid. "
                    "Please check your API key, secret, and passphrase."
                )
            if "permission" in lower:
                return (
                    "API key lacks required permissions. "
                    "Please ensure your API key has read access to balances."
                )
            if "network" in lower or "unavailable" in lower:
                return (
                    "Could not reach the exchange to verify credentials. "
                    "The exchange may be temporarily unavailable. Try again later."
                )
            return f"Credential verification failed: {error}"
        return None

    async def _create_integration(self, payload: dict[str, Any]) -> InputProcessResult:
        try:
            created = await self.api_repo.create_integration(payload)
        except Exception as exc:
            return InputProcessResult(
                success=False, error_text=self._integration_error_text(exc)
            )

        integration_id = self._safe_int(created.get("id"), default=-1)
        if integration_id < 0:
            return InputProcessResult(
                success=True, next_route=ROUTE_INTEGRATIONS, next_payload={}
            )

        job_id: int | None = None
        try:
            job_id = await self.refresh_integration(integration_id)
        except Exception:
            job_id = None

        return InputProcessResult(
            success=True,
            next_route=ROUTE_INTEGRATION_DETAIL,
            next_payload={"id": integration_id}
            | ({"job_id": job_id} if job_id is not None else {}),
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
    def _looks_like_solana_address(value: str) -> bool:
        return bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", value.strip()))

    @staticmethod
    def _looks_like_tron_address(value: str) -> bool:
        return bool(re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", value.strip()))

    @staticmethod
    def _looks_like_ton_address(value: str) -> bool:
        return bool(re.fullmatch(r"(EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}", value.strip()))

    @staticmethod
    def _looks_like_sui_address(value: str) -> bool:
        return bool(re.fullmatch(r"0x[0-9a-fA-F]{64}", value.strip()))

    @staticmethod
    def _short_address(value: str) -> str:
        return _short_addr(value)

    @classmethod
    def _default_dex_label(cls, wallet_address: str, chain: str = "") -> str:
        return _dex_name(wallet_address, chain)

    @staticmethod
    def _default_cex_label(exchange_code: str, account_ref: str | None = None) -> str:
        return _cex_name(exchange_code, account_ref)

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _stale_marker(is_stale: bool) -> str:
        return " ⚠️" if is_stale else ""

    @staticmethod
    def _select_exchange_rows(
        exchanges: dict[str, dict[str, Any]],
        hide_small: bool,
        name_map: dict[str, str] | None = None,
        stale_services: set[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Return (button_labels, service_keys) sorted by balance desc.

        button_labels — labels with \u00b7 balance for tab buttons
        service_keys — raw service keys used for navigation payloads
        """
        rows: list[tuple[str, str, float, bool]] = []
        normalized_stale = {
            str(item).strip().lower() for item in (stale_services or set()) if item
        }
        for service_key, acc in sorted(
            exchanges.items(),
            key=lambda item: item[1].get("total_usd", 0),
            reverse=True,
        ):
            total = float(acc.get("total_usd", 0) or 0)
            if hide_small and total < settings.hide_small_balance_threshold:
                continue
            is_stale = str(service_key).strip().lower() in normalized_stale
            base_name = (name_map or {}).get(service_key) or _service_label(service_key)
            button = _btn_label(base_name, total, stale=is_stale)
            rows.append((button, service_key, total, is_stale))

        labels = [label for label, _, _, _ in rows]
        names = [svc for _, svc, _, _ in rows]
        return labels, names

    @staticmethod
    def _wallet_service_name(
        identifier: str, provider: str = "okx_wallet", chain: str = ""
    ) -> str:
        """Return canonical service key for a DEX wallet.

        Mapping:
          okx_wallet + EVM address → evm_{addr}
          okx_wallet + Solana      → sol_{addr}
          tron_ton   + TRON        → tron_{addr}
          tron_ton   + TON         → ton_{addr}
          sui                      → sui_{addr}
        """
        addr = (identifier or "").strip().lower()
        p = (provider or "").strip().lower()
        c = (chain or "").strip().lower()
        if p == "sui":
            return f"sui_{addr}"
        if p == "tron_ton":
            if c == "tron":
                return f"tron_{addr}"
            return f"ton_{addr}"
        # okx_wallet: distinguish by chain
        if c == "solana":
            return f"sol_{addr}"
        return f"evm_{addr}"

    @staticmethod
    def _base_service_name(service: str) -> str:
        return str(service).split("#", 1)[0]

    # Known service-name prefixes for wallet providers.
    # IMPORTANT: longer/more-specific prefixes must come before shorter ones
    # so that "tron_ton_" is stripped before "tron_" catches it.
    _WALLET_SERVICE_PREFIXES = (
        # legacy (longer) — must be before their canonical substrings
        "okx_wallet_",
        "tron_ton_",
        # canonical
        "evm_",
        "sol_",
        "tron_",
        "ton_",
        "sui_",
    )

    @classmethod
    def _wallet_address_from_service(cls, service: str) -> str:
        base = cls._base_service_name(service)
        for prefix in cls._WALLET_SERVICE_PREFIXES:
            if base.startswith(prefix):
                return base.removeprefix(prefix)
        return ""

    @classmethod
    def _chain_from_service(cls, service: str) -> str:
        """Infer chain name from service key prefix for auto-label fallback.

        evm_      → ethereum  (resolves to EVM prefix via _CHAIN_LABELS)
        sol_      → solana
        tron_     → tron
        ton_      → ton
        sui_      → sui
        okx_wallet_  → ethereum (legacy EVM)
        tron_ton_    → tron or ton — detected from embedded address pattern:
                       T[A-Z0-9]{33} → tron, UQ/EQ → ton
        """
        import re as _re

        base = cls._base_service_name(service)

        # Legacy tron_ton_ must be checked FIRST — it starts with "tron_"
        # so the canonical "tron_" check below would incorrectly match it.
        if base.startswith("tron_ton_"):
            addr = base.removeprefix("tron_ton_")
            # TON addresses start with uq/eq (lower-cased here)
            if addr.startswith("uq") or addr.startswith("eq"):
                return "ton"
            # TRON addresses start with 't' and are 34 chars
            if addr.startswith("t") and len(addr) == 34:
                return "tron"
            return "tron"

        if base.startswith("okx_wallet_"):
            return "ethereum"

        # Canonical prefixes
        _CANONICAL: tuple[tuple[str, str], ...] = (
            ("ton_", "ton"),
            ("tron_", "tron"),
            ("evm_", "ethereum"),
            ("sol_", "solana"),
            ("sui_", "sui"),
        )
        for prefix, chain in _CANONICAL:
            if base.startswith(prefix):
                return chain

        return ""

    def _display_name_for_integration(self, item: dict[str, Any]) -> str:
        return _integration_label(item)

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
        stale_services: set[str] | None = None,
    ) -> list[str]:
        rows: list[tuple[str, float]] = []
        normalized_stale = {
            str(item).strip().lower() for item in (stale_services or set()) if item
        }
        for service, acc in sorted(
            wallets.items(),
            key=lambda item: item[1].get("total_usd", 0),
            reverse=True,
        ):
            total = float(acc.get("total_usd", 0) or 0)
            if hide_small and total < settings.hide_small_balance_threshold:
                continue
            base_name = (name_map or {}).get(service) or _service_label(service)
            is_stale = str(service).strip().lower() in normalized_stale
            button = _btn_label(base_name, total, stale=is_stale)
            rows.append((button, total))
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
        for service, acc in spot.items():
            display = self._display_name_for_service(
                service, parsed, name_map=name_map, include_total=False
            )
            sources[service] = (display, float(acc.get("total_usd", 0) or 0))
        for item in integrations:
            if not isinstance(item, dict) or not bool(item.get("is_active")):
                continue
            if str(item.get("kind") or "").strip().lower() != "cex":
                continue
            service = self._service_name_from_integration(item)
            if not service or service in sources:
                continue
            sources[service] = (self._display_name_for_integration(item), 0.0)
        ordered = sorted(
            sources.items(), key=lambda item: (item[1][1], item[1][0]), reverse=True
        )
        return [label for _, (label, _) in ordered]

    def _transaction_source_services(
        self,
        parsed: dict[str, Any],
        integrations: list[dict[str, Any]],
    ) -> list[str]:
        sources: dict[str, tuple[str, float]] = {}
        spot = parsed.get("spot_exchanges") or {}
        for service, acc in spot.items():
            sources[service] = (
                self._display_name_for_service(service, parsed, include_total=False),
                float(acc.get("total_usd", 0) or 0),
            )
        for item in integrations:
            if not isinstance(item, dict) or not bool(item.get("is_active")):
                continue
            if str(item.get("kind") or "").strip().lower() != "cex":
                continue
            service = self._service_name_from_integration(item)
            if not service or service in sources:
                continue
            sources[service] = (self._display_name_for_integration(item), 0.0)
        ordered = sorted(
            sources.items(), key=lambda item: (item[1][1], item[1][0]), reverse=True
        )
        return [service for service, _ in ordered]

    def _display_name_for_service(
        self,
        service: str,
        parsed: dict[str, Any],
        *,
        name_map: dict[str, str] | None = None,
        include_total: bool = True,
    ) -> str:
        resolved_name = (name_map or {}).get(service)
        if service in (parsed.get("spot_exchanges") or {}):
            total = float(
                (parsed.get("spot_exchanges") or {})
                .get(service, {})
                .get("total_usd", 0)
                or 0
            )
            base = self._base_service_name(service)
            label = resolved_name or self._default_cex_label(base)
            return f"{label}{': ' + msg.format_usd(total) if include_total else ''}"
        if service in (parsed.get("dex_wallets") or {}):
            total = float(
                (parsed.get("dex_wallets") or {}).get(service, {}).get("total_usd", 0)
                or 0
            )
            label = resolved_name or self._default_dex_label(
                self._wallet_address_from_service(service),
                self._chain_from_service(service),
            )
            return f"{label}{': ' + msg.format_usd(total) if include_total else ''}"
        return resolved_name or self._base_service_name(service)

    def _service_name_from_integration(self, item: dict[str, Any]) -> str:
        kind = str(item.get("kind") or "").strip().lower()
        if kind == "cex":
            return str(item.get("exchange_code") or "").strip().lower()
        wallet_address = str(item.get("wallet_address") or "").strip().lower()
        if wallet_address:
            provider = str(item.get("provider") or "okx_wallet").strip().lower()
            chain = str(item.get("chain") or "").strip().lower()
            return self._wallet_service_name(wallet_address, provider, chain)
        return ""

    def _build_service_name_map(
        self, integrations: list[dict[str, Any]]
    ) -> dict[str, str]:
        """Map service key to normalized display labels for integrations."""
        return _build_name_map(integrations)

    @staticmethod
    def _integration_label(item: dict[str, Any]) -> str:
        name = str(item.get("display_name") or item.get("name") or "").strip()
        if name:
            return name
        service = str(
            item.get("exchange_code")
            or item.get("wallet_address")
            or item.get("provider")
            or "source"
        ).strip()
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
