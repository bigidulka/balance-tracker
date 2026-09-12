from typing import Any

from app.models.balance import Integration
from app.services.ccxt_manager import ccxt_manager
from app.services.integrations.provider import IntegrationProvider, ProviderRefreshResult


class CCXTIntegrationProvider(IntegrationProvider):
    provider_name = "ccxt"

    @staticmethod
    def _config_override_from_payload(payload: dict[str, Any]) -> dict[str, str] | None:
        api_key = str(payload.get("api_key") or "").strip()
        api_secret = str(payload.get("api_secret") or "").strip()
        if not api_key or not api_secret:
            return None

        config = {"apiKey": api_key, "secret": api_secret}
        api_password = str(payload.get("api_password") or "").strip()
        api_uid = str(payload.get("api_uid") or "").strip()
        if api_password:
            config["password"] = api_password
        if api_uid:
            config["uid"] = api_uid
        return config

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

        config_override = self._config_override_from_payload(payload)
        if config_override is None:
            return ProviderRefreshResult(
                status="failed",
                message="api_key and api_secret are required for ccxt provider",
            )

        balance = await ccxt_manager.fetch_balance(
            exchange_id,
            config_override=config_override,
        )
        payload_balance = balance.model_dump(mode="json")
        payload_balance["integration_id"] = integration.id
        if not balance.actual:
            return ProviderRefreshResult(
                status="failed",
                message=(
                    "Exchange returned partial balance data"
                    if balance.accounts or balance.assets
                    else "Exchange returned no verified balance data"
                ),
                data={
                    "organization_id": organization_id,
                    "integration_id": integration.id,
                    "exchange_id": exchange_id,
                    "balance": payload_balance,
                },
            )
        return ProviderRefreshResult(
            status="ok",
            data={
                "organization_id": organization_id,
                "integration_id": integration.id,
                "exchange_id": exchange_id,
                "balance": payload_balance,
            },
        )
