from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BillingPlanSchema(BaseModel):
    code: str
    name: str
    price_monthly: float = 0.0
    currency: str = "USD"
    policy: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class BillingPlansResponse(BaseModel):
    plans: list[BillingPlanSchema] = Field(default_factory=list)


class CurrentSubscriptionResponse(BaseModel):
    plan: dict[str, Any]
    policy: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    throttling: dict[str, Any] = Field(default_factory=dict)
    background: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    wallet: dict[str, Any] = Field(default_factory=dict)
    last_refresh_at: str | None = None


class SwitchPlanRequest(BaseModel):
    plan_code: str


class SwitchPlanResponse(BaseModel):
    status: str
    changed: bool
    plan_code: str


class BillingBalanceResponse(BaseModel):
    currency: str = "USD"
    available: float = 0.0
    entries: list[dict[str, Any]] = Field(default_factory=list)


class CreateInvoiceRequest(BaseModel):
    invoice_type: Literal["balance_topup", "plan_purchase"] = "balance_topup"
    amount_usd: float | None = None
    plan_code: str | None = None

    @model_validator(mode="after")
    def _validate_payload(self) -> "CreateInvoiceRequest":
        if self.invoice_type == "balance_topup":
            if self.amount_usd is None or float(self.amount_usd or 0.0) <= 0:
                raise ValueError("amount_usd must be positive for balance top-up")
            return self

        if self.invoice_type == "plan_purchase":
            if not (self.plan_code or "").strip():
                raise ValueError("plan_code is required for plan purchase")
            if self.amount_usd is not None and float(self.amount_usd or 0.0) <= 0:
                raise ValueError("amount_usd must be positive when provided")
            return self

        raise ValueError("Unsupported invoice type")


class InvoiceSchema(BaseModel):
    id: int
    status: str
    invoice_type: str
    amount: float
    currency: str = "USD"
    asset: str | None = None
    plan_code: str | None = None
    pay_url: str | None = None
    bot_invoice_url: str | None = None
    external_invoice_id: str | None = None
    description: str | None = None
    paid_amount: float | None = None
    paid_asset: str | None = None
    paid_usd_amount: float | None = None
    expires_at: str | None = None
    paid_at: str | None = None
    created_at: str | None = None


class InvoiceListResponse(BaseModel):
    items: list[InvoiceSchema] = Field(default_factory=list)


class RedeemPromoRequest(BaseModel):
    code: str


class RedeemPromoResponse(BaseModel):
    status: str
    reward_type: str
    reward_value: float
    reward_currency: str = "USD"
    plan_code: str | None = None
