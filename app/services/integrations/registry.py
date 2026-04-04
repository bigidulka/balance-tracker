from app.services.integrations.ccxt_provider import CCXTIntegrationProvider
from app.services.integrations.okx_wallet_provider import OKXWalletIntegrationProvider
from app.services.integrations.provider import IntegrationProvider

_PROVIDER_REGISTRY: dict[str, IntegrationProvider] = {
    "ccxt": CCXTIntegrationProvider(),
    "exchange": CCXTIntegrationProvider(),
    "okx_wallet": OKXWalletIntegrationProvider(),
}


def get_provider(provider_name: str) -> IntegrationProvider:
    normalized = (provider_name or "").strip().lower()
    if normalized not in _PROVIDER_REGISTRY:
        raise ValueError(f"Unsupported integration provider: {provider_name}")
    return _PROVIDER_REGISTRY[normalized]
