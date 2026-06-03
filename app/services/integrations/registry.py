from app.services.integrations.ccxt_provider import CCXTIntegrationProvider
from app.services.integrations.cryptobot_provider import CryptoBotIntegrationProvider
from app.services.integrations.debank_provider import DeBankIntegrationProvider
from app.services.integrations.okx_wallet_provider import OKXWalletIntegrationProvider
from app.services.integrations.tron_ton_provider import TronTonIntegrationProvider
from app.services.integrations.sui_provider import SuiIntegrationProvider
from app.services.integrations.provider import IntegrationProvider

_PROVIDER_REGISTRY: dict[str, IntegrationProvider] = {
    "ccxt": CCXTIntegrationProvider(),
    "exchange": CCXTIntegrationProvider(),
    "cryptobot": CryptoBotIntegrationProvider(),
    "debank": DeBankIntegrationProvider(),
    "okx_wallet": OKXWalletIntegrationProvider(),
    "tron_ton": TronTonIntegrationProvider(),
    "sui": SuiIntegrationProvider(),
}


def get_provider(provider_name: str) -> IntegrationProvider:
    normalized = (provider_name or "").strip().lower()
    if normalized not in _PROVIDER_REGISTRY:
        raise ValueError(f"Unsupported integration provider: {provider_name}")
    return _PROVIDER_REGISTRY[normalized]
