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

    @model_validator(mode="after")
    def validate_identity(self) -> "IntegrationCreateRequest":
        if self.kind == "cex":
            if not self.exchange_code or not self.account_ref:
                raise ValueError("exchange_code and account_ref are required for cex kind")
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


class IntegrationRefreshResponse(BaseModel):
    status: str
    message: str
    integration_id: int
    job_id: int
    job_status: str
