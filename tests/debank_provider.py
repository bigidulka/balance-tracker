from typing import Any

from app.models.balance import Integration
from tests.debank_sdk import debank_sdk
from app.services.integrations.provider import (
    IntegrationProvider,
    ProviderRefreshResult,
)


class DeBankIntegrationProvider(IntegrationProvider):
    provider_name = "debank"

    async def refresh(
        self,
        organization_id: int,
        integration: Integration,
        payload: dict[str, Any] | None = None,
    ) -> ProviderRefreshResult:
        payload = payload or {}
        wallet_address = payload.get("wallet_address") or integration.wallet_address

        if not wallet_address:
            return ProviderRefreshResult(
                status="failed",
                message="wallet_address is required for debank provider",
            )

        try:
            balance = await debank_sdk.get_portfolio_data(wallet_address)
            return ProviderRefreshResult(
                status="ok",
                data={
                    "organization_id": organization_id,
                    "integration_id": integration.id,
                    "wallet_address": wallet_address,
                    "balance": balance.model_dump(),
                },
            )
        except Exception as e:
            return ProviderRefreshResult(
                status="failed",
                message=f"Error fetching DeBank balance: {str(e)}",
                data={
                    "organization_id": organization_id,
                    "integration_id": integration.id,
                },
            )
