from __future__ import annotations

from typing import Any

from app.models.balance import Integration
from app.services.crypto_bot_app_client import (
    CryptoBotAppClientError,
    crypto_bot_app_client,
)
from app.services.integrations.provider import IntegrationProvider, ProviderRefreshResult


class CryptoBotIntegrationProvider(IntegrationProvider):
    provider_name = "cryptobot"

    async def refresh(
        self,
        organization_id: int,
        integration: Integration,
        payload: dict[str, Any] | None = None,
    ) -> ProviderRefreshResult:
        payload = payload or {}
        api_token = str(payload.get("api_token") or "").strip()
        if not api_token:
            return ProviderRefreshResult(
                status="failed",
                message="api_token is required for cryptobot provider",
            )

        try:
            balance = await crypto_bot_app_client.fetch_balance(
                api_token,
                integration_id=integration.id,
                service=f"cryptobot:{integration.id}",
            )
        except CryptoBotAppClientError as exc:
            return ProviderRefreshResult(status="failed", message=str(exc))

        payload_balance = balance.model_dump(mode="json")
        payload_balance["integration_id"] = integration.id
        return ProviderRefreshResult(
            status="ok",
            data={
                "organization_id": organization_id,
                "integration_id": integration.id,
                "balance": payload_balance,
            },
        )
