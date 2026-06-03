from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class IntegrationCreateRequest(BaseModel):
    provider: str
    name: str
    kind: Literal["cex", "dex"]
    exchange_code: str | None = None
    account_ref: str | None = None
    wallet_address: str | None = None
    chain: str | None = None
    api_key: str | None = None
    api_secret: str | None = None
    api_password: str | None = None
    api_uid: str | None = None
    api_token: str | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> "IntegrationCreateRequest":
        if self.kind == "cex":
            # For cryptobot providers, exchange_code auto-fills
            provider_lower = str(self.provider or "").strip().lower()
            is_cryptobot = provider_lower in {"cryptobot", "cryptobot_apps"}
            if is_cryptobot and not self.exchange_code:
                self.exchange_code = "cryptobot"
            if not self.exchange_code or not self.account_ref:
                raise ValueError(
                    "exchange_code and account_ref are required for cex kind"
                )
        elif self.kind == "dex":
            if not self.wallet_address or not self.chain:
                raise ValueError("wallet_address and chain are required for dex kind")
        return self



class IntegrationResponse(BaseModel):
    id: int
    provider: str
    name: str
    kind: str
    exchange_code: str | None = None
    account_ref: str | None = None
    wallet_address: str | None = None
    chain: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class IntegrationUpdateRequest(BaseModel):
    name: str


class IntegrationRefreshResponse(BaseModel):
    status: str
    message: str
    integration_id: int
    job_id: int
    job_status: str


class IntegrationVerifyRequest(BaseModel):
    exchange_code: str
    api_key: str | None = None
    api_secret: str | None = None
    api_password: str | None = None
    api_uid: str | None = None
    api_token: str | None = None


class IntegrationVerifyResponse(BaseModel):
    ok: bool
    error: str | None = None
