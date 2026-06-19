from app.services.okx_wallet import OKXWalletService
from app.services.sui_service import SuiService
from app.services.tron_ton_service import TronTonService


def service_key_for_integration(integration: object) -> str:
    kind = str(getattr(integration, "kind", "") or "").strip().lower()
    if kind == "cex":
        return str(getattr(integration, "exchange_code", "") or "").strip().lower()

    wallet_address = str(getattr(integration, "wallet_address", "") or "").strip()
    provider = str(getattr(integration, "provider", "") or "").strip().lower()
    if wallet_address:
        if provider == "tron_ton":
            return TronTonService.service_name_for(wallet_address)
        if provider == "sui":
            return SuiService.service_name_for(wallet_address)
        return OKXWalletService.service_name_for(wallet_address)

    chain = str(getattr(integration, "chain", "") or "").strip().lower()
    if chain:
        return chain
    return provider
