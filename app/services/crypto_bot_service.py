from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.http import request_proxy_kwargs, session_kwargs
from app.models.balance import BillingEvent, PaymentInvoice
from app.services.billing_service import BillingService
from app.services.ledger_service import LedgerService

settings = get_settings()


class CryptoBotServiceError(RuntimeError):
    pass


class CryptoBotService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self._http_session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                **session_kwargs(aiohttp.ClientTimeout(total=20))
            )
        return self._http_session

    def _ensure_enabled(self) -> None:
        if not settings.crypto_bot_api_token:
            raise CryptoBotServiceError("Crypto Bot API token is not configured")

    async def _request(self, method_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        session = await self._get_http_session()
        headers = {
            "Crypto-Pay-API-Token": settings.crypto_bot_api_token,
            "Content-Type": "application/json",
        }
        async with session.post(
            f"{settings.crypto_bot_api_base_url.rstrip('/')}/{method_name}",
            headers=headers,
            json=payload,
            **request_proxy_kwargs(),
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise CryptoBotServiceError(
                    f"Crypto Bot API {method_name} failed with status {response.status}: {data}"
                )
            if not isinstance(data, dict) or not data.get("ok"):
                raise CryptoBotServiceError(f"Crypto Bot API {method_name} returned error: {data}")
            result = data.get("result")
            if not isinstance(result, dict):
                raise CryptoBotServiceError(f"Crypto Bot API {method_name} returned invalid payload")
            return result

    async def create_balance_topup_invoice(
        self,
        *,
        organization_id: int,
        user_id: int | None,
        amount_usd: float,
        description: str | None = None,
    ) -> PaymentInvoice:
        value = round(float(amount_usd or 0.0), 2)
        if value <= 0:
            raise CryptoBotServiceError("Invoice amount must be positive")

        external_payload = f"org:{organization_id}:topup:{secrets.token_hex(8)}"
        result = await self._request(
            "createInvoice",
            {
                "asset": settings.crypto_bot_invoice_asset,
                "amount": f"{value:.2f}",
                "description": description or f"Balance top-up for organization {organization_id}",
                "payload": external_payload,
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": 3600,
            },
        )

        invoice = PaymentInvoice(
            organization_id=organization_id,
            created_by_user_id=user_id,
            provider="cryptobot",
            status=str(result.get("status") or "pending"),
            invoice_type="balance_topup",
            currency="USD",
            amount=value,
            asset=str(result.get("asset") or settings.crypto_bot_invoice_asset),
            external_invoice_id=str(result.get("invoice_id") or ""),
            external_payload=external_payload,
            pay_url=result.get("pay_url"),
            bot_invoice_url=result.get("bot_invoice_url"),
            description=description or f"Balance top-up {value:.2f} USD",
            metadata_json=result,
            expires_at=self._parse_datetime(result.get("expiration_date"))
            or datetime.now(timezone.utc) + timedelta(hours=1),
        )
        self.session.add(invoice)
        self.session.add(
            BillingEvent(
                organization_id=organization_id,
                event_type="payment.invoice_created",
                provider="cryptobot",
                provider_event_id=invoice.external_invoice_id,
                amount=value,
                currency="USD",
                payload=result,
            )
        )
        await self.session.commit()
        await self.session.refresh(invoice)
        return invoice

    async def create_plan_purchase_invoice(
        self,
        *,
        organization_id: int,
        user_id: int | None,
        plan_code: str,
        amount_usd: float | None = None,
        description: str | None = None,
    ) -> PaymentInvoice:
        billing = BillingService(self.session)
        plan = await billing.resolve_plan(plan_code)
        if plan is None:
            raise CryptoBotServiceError("Plan not found")

        resolved_amount = round(
            float(amount_usd if amount_usd is not None else plan.price_monthly or 0.0),
            2,
        )
        if resolved_amount <= 0:
            raise CryptoBotServiceError("Plan price must be positive")

        external_payload = f"org:{organization_id}:plan:{plan.code}:{secrets.token_hex(8)}"
        result = await self._request(
            "createInvoice",
            {
                "asset": settings.crypto_bot_invoice_asset,
                "amount": f"{resolved_amount:.2f}",
                "description": description or f"{plan.name} plan purchase for organization {organization_id}",
                "payload": external_payload,
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": 3600,
            },
        )

        invoice = PaymentInvoice(
            organization_id=organization_id,
            created_by_user_id=user_id,
            provider="cryptobot",
            status=str(result.get("status") or "pending"),
            invoice_type="plan_purchase",
            currency="USD",
            amount=resolved_amount,
            asset=str(result.get("asset") or settings.crypto_bot_invoice_asset),
            external_invoice_id=str(result.get("invoice_id") or ""),
            external_payload=external_payload,
            pay_url=result.get("pay_url"),
            bot_invoice_url=result.get("bot_invoice_url"),
            description=description or f"{plan.name} plan purchase {resolved_amount:.2f} USD",
            metadata_json={
                "invoice_type": "plan_purchase",
                "plan_code": plan.code,
                "plan_name": plan.name,
                "requested_amount_usd": resolved_amount,
                "provider_response": result,
            },
            expires_at=self._parse_datetime(result.get("expiration_date"))
            or datetime.now(timezone.utc) + timedelta(hours=1),
        )
        self.session.add(invoice)
        self.session.add(
            BillingEvent(
                organization_id=organization_id,
                event_type="payment.invoice_created",
                provider="cryptobot",
                provider_event_id=invoice.external_invoice_id,
                amount=resolved_amount,
                currency="USD",
                payload=result,
            )
        )
        await self.session.commit()
        await self.session.refresh(invoice)
        return invoice

    async def get_invoice_by_id(self, invoice_id: int, organization_id: int) -> PaymentInvoice | None:
        result = await self.session.execute(
            select(PaymentInvoice).where(
                and_(
                    PaymentInvoice.id == invoice_id,
                    PaymentInvoice.organization_id == organization_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def list_invoices(
        self,
        organization_id: int,
        *,
        limit: int = 10,
    ) -> list[PaymentInvoice]:
        result = await self.session.execute(
            select(PaymentInvoice)
            .where(PaymentInvoice.organization_id == organization_id)
            .order_by(PaymentInvoice.created_at.desc(), PaymentInvoice.id.desc())
            .limit(max(1, min(limit, 50)))
        )
        return list(result.scalars().all())

    async def refresh_invoice_status(self, invoice: PaymentInvoice) -> PaymentInvoice:
        if not invoice.external_invoice_id:
            return invoice
        result = await self._request(
            "getInvoices",
            {"invoice_ids": [invoice.external_invoice_id]},
        )
        items = result.get("items") if isinstance(result.get("items"), list) else []
        payload = items[0] if items else result
        if not isinstance(payload, dict):
            raise CryptoBotServiceError("Crypto Bot getInvoices returned empty payload")
        await self._apply_invoice_payload(invoice, payload)
        return invoice

    async def apply_webhook_update(
        self,
        *,
        payload: dict[str, Any],
        external_event_id: str,
    ) -> PaymentInvoice | None:
        invoice_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        if not isinstance(invoice_payload, dict):
            return None
        external_invoice_id = str(
            invoice_payload.get("invoice_id")
            or invoice_payload.get("id")
            or external_event_id
            or ""
        )
        if not external_invoice_id:
            return None
        result = await self.session.execute(
            select(PaymentInvoice).where(
                and_(
                    PaymentInvoice.provider == "cryptobot",
                    PaymentInvoice.external_invoice_id == external_invoice_id,
                )
            )
        )
        invoice = result.scalar_one_or_none()
        if invoice is None:
            return None
        await self._apply_invoice_payload(invoice, invoice_payload)
        return invoice

    async def _apply_invoice_payload(self, invoice: PaymentInvoice, payload: dict[str, Any]) -> None:
        billing = BillingService(self.session)
        invoice.status = str(payload.get("status") or invoice.status or "pending").lower()
        invoice.asset = str(payload.get("asset") or invoice.asset or settings.crypto_bot_invoice_asset)
        invoice.pay_url = str(payload.get("pay_url") or invoice.pay_url or "")
        invoice.bot_invoice_url = str(payload.get("bot_invoice_url") or invoice.bot_invoice_url or "")
        invoice.paid_asset = self._clean_optional_str(payload.get("paid_asset"))
        invoice.paid_amount = self._safe_float(payload.get("paid_amount"))
        invoice.paid_usd_amount = self._safe_float(payload.get("paid_usd_rate")) * float(invoice.paid_amount or 0.0)
        invoice.expires_at = self._parse_datetime(payload.get("expiration_date")) or invoice.expires_at
        invoice.paid_at = self._parse_datetime(payload.get("paid_at")) or invoice.paid_at
        existing_metadata = dict(invoice.metadata_json) if isinstance(invoice.metadata_json, dict) else {}
        invoice.metadata_json = {**existing_metadata, **payload}

        if invoice.status == "paid":
            if not await billing.has_billing_event(
                organization_id=invoice.organization_id,
                provider="cryptobot",
                provider_event_id=invoice.external_invoice_id,
                event_type="payment.invoice_paid",
            ):
                if invoice.invoice_type == "plan_purchase":
                    plan_code = self._extract_plan_code(invoice)
                    if not plan_code:
                        raise CryptoBotServiceError("Paid plan invoice is missing plan code")
                    switched = await billing.switch_subscription_plan(
                        organization_id=invoice.organization_id,
                        plan_code=plan_code,
                    )
                    billing.add_billing_event(
                        organization_id=invoice.organization_id,
                        event_type="payment.invoice_paid",
                        provider="cryptobot",
                        provider_event_id=invoice.external_invoice_id,
                        amount=float(invoice.amount or 0.0),
                        currency="USD",
                        payload={
                            "invoice": payload,
                            "invoice_type": invoice.invoice_type,
                            "plan_code": plan_code,
                            "switched": switched,
                        },
                    )
                else:
                    ledger = LedgerService(self.session)
                    source_id = str(invoice.id)
                    if not await ledger.has_source_entry(
                        organization_id=invoice.organization_id,
                        source_type="invoice",
                        source_id=source_id,
                    ):
                        await ledger.add_entry(
                            organization_id=invoice.organization_id,
                            created_by_user_id=invoice.created_by_user_id,
                            entry_type="credit",
                            amount=float(invoice.amount or 0.0),
                            currency="USD",
                            source_type="invoice",
                            source_id=source_id,
                            note="Crypto Bot top-up",
                            metadata={"provider": "cryptobot", "external_invoice_id": invoice.external_invoice_id},
                        )
                    billing.add_billing_event(
                        organization_id=invoice.organization_id,
                        event_type="payment.invoice_paid",
                        provider="cryptobot",
                        provider_event_id=invoice.external_invoice_id,
                        amount=float(invoice.amount or 0.0),
                        currency="USD",
                        payload=payload,
                    )

        await self.session.commit()
        await self.session.refresh(invoice)

    @staticmethod
    def _extract_plan_code(invoice: PaymentInvoice) -> str | None:
        metadata = invoice.metadata_json if isinstance(invoice.metadata_json, dict) else {}
        plan_code = metadata.get("plan_code")
        if isinstance(plan_code, str) and plan_code.strip():
            normalized = plan_code.strip().lower()
            return "pro" if normalized == "full" else normalized
        return None

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean_optional_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return datetime.fromisoformat(text)
        except ValueError:
            return None
