from typing import Any

from app.models.balance import Integration
from app.services.integrations.provider import IntegrationProvider, ProviderRefreshResult
from app.services.okx_wallet import okx_wallet_service


class OKXWalletIntegrationProvider(IntegrationProvider):
    provider_name = "okx_wallet"

    async def refresh(
        self,
        organization_id: int,
        integration: Integration,
        payload: dict[str, Any] | None = None,
    ) -> ProviderRefreshResult:
        payload = payload or {}
        wallet_identifier = (
            payload.get("wallet_address")
            or integration.wallet_address
            or integration.external_id
        )
        if not wallet_identifier:
            return ProviderRefreshResult(
                status="failed",
                message="wallet_address is required for okx_wallet provider",
            )

        balance = await okx_wallet_service.fetch_wallet_balance(wallet_identifier)
        payload_balance = balance.model_dump(mode="json")
        payload_balance["integration_id"] = integration.id
        return ProviderRefreshResult(
            status="ok",
            data={
                "organization_id": organization_id,
                "integration_id": integration.id,
                "wallet_identifier": wallet_identifier,
                "balance": payload_balance,
            },
        )
