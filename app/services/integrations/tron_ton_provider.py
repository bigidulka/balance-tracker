from typing import Any

from app.models.balance import Integration
from app.services.integrations.provider import (
    IntegrationProvider,
    ProviderRefreshResult,
)
from app.services.tron_ton_service import tron_ton_service


class TronTonIntegrationProvider(IntegrationProvider):
    """
    Integration provider for TRON and TON wallets.

    Uses TronGrid (TRON) and tonapi.io (TON) — both are free public APIs
    that require no API key.
    """

    provider_name = "tron_ton"

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
                message="wallet_address is required for tron_ton provider",
            )

        balance = await tron_ton_service.fetch_wallet_balance(wallet_identifier)
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
