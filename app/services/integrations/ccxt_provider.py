from typing import Any

from app.models.balance import Integration
from app.services.ccxt_manager import ccxt_manager
from app.services.integrations.provider import IntegrationProvider, ProviderRefreshResult


class CCXTIntegrationProvider(IntegrationProvider):
    provider_name = "ccxt"

    async def refresh(
        self,
        organization_id: int,
        integration: Integration,
        payload: dict[str, Any] | None = None,
    ) -> ProviderRefreshResult:
        payload = payload or {}
        exchange_id = (
            payload.get("exchange_id")
            or integration.exchange_code
            or integration.external_id
            or integration.name
            or integration.provider
        )

        balance = await ccxt_manager.fetch_balance(exchange_id)
        payload_balance = balance.model_dump(mode="json")
        payload_balance["integration_id"] = integration.id
        return ProviderRefreshResult(
            status="ok",
            data={
                "organization_id": organization_id,
                "integration_id": integration.id,
                "exchange_id": exchange_id,
                "balance": payload_balance,
            },
        )
