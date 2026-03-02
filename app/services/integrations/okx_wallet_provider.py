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
        account_id = payload.get("account_id") or integration.external_id
        if not account_id:
            return ProviderRefreshResult(
                status="failed",
                message="account_id is required for okx_wallet provider",
            )

        balance = await okx_wallet_service.fetch_wallet_balance(account_id)
        return ProviderRefreshResult(
            status="ok",
            data={
                "organization_id": organization_id,
                "integration_id": integration.id,
                "account_id": account_id,
                "balance": balance.model_dump(),
            },
        )
