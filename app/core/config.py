from pydantic import Field
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional
import json


class ExchangeKeys(BaseSettings):
    api_key: str = ""
    secret: str = ""
    password: Optional[str] = None
    uid: Optional[str] = None


class Settings(BaseSettings):
    database_url: str = Field(default="sqlite+aiosqlite:///./data/balance_tracker.db")

    proxy_host: str = Field(default="")
    proxy_port: int = Field(default=3128)
    proxy_username: str = Field(default="")
    proxy_password: str = Field(default="")

    binance_api_key: str = Field(default="")
    binance_secret: str = Field(default="")

    bitget_api_key: str = Field(default="")
    bitget_secret: str = Field(default="")
    bitget_password: str = Field(default="")

    bybit_api_key: str = Field(default="")
    bybit_secret: str = Field(default="")

    gateio_api_key: str = Field(default="")
    gateio_secret: str = Field(default="")

    htx_api_key: str = Field(default="")
    htx_secret: str = Field(default="")

    kucoin_api_key: str = Field(default="")
    kucoin_secret: str = Field(default="")
    kucoin_password: str = Field(default="")

    mexc_api_key: str = Field(default="")
    mexc_secret: str = Field(default="")

    okx_api_key: str = Field(default="")
    okx_secret: str = Field(default="")
    okx_password: str = Field(default="")

    bitmart_api_key: str = Field(default="")
    bitmart_secret: str = Field(default="")
    bitmart_uid: str = Field(default="")

    poloniex_api_key: str = Field(default="")
    poloniex_secret: str = Field(default="")

    lbank_api_key: str = Field(default="")
    lbank_secret: str = Field(default="")

    coinex_api_key: str = Field(default="")
    coinex_secret: str = Field(default="")

    bingx_api_key: str = Field(default="")
    bingx_secret: str = Field(default="")

    xt_api_key: str = Field(default="")
    xt_secret: str = Field(default="")

    okx_wallet_account_ids: str = Field(
        default='["6555B26D-2FEC-4CBD-AB1A-E410BB9D400D"]'
    )

    balance_cache_ttl: int = Field(default=60)
    request_timeout: int = Field(default=30)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def proxy_url(self) -> Optional[str]:
        if self.proxy_host and self.proxy_username:
            return f"http://{self.proxy_username}:{self.proxy_password}@{self.proxy_host}:{self.proxy_port}"
        elif self.proxy_host:
            return f"http://{self.proxy_host}:{self.proxy_port}"
        return None

    @property
    def okx_wallet_accounts(self) -> list[str]:
        try:
            return json.loads(self.okx_wallet_account_ids)
        except (json.JSONDecodeError, TypeError):
            return []

    def get_exchange_config(self, exchange_id: str) -> dict:
        configs = {
            "binance": {
                "apiKey": self.binance_api_key,
                "secret": self.binance_secret,
            },
            "bitget": {
                "apiKey": self.bitget_api_key,
                "secret": self.bitget_secret,
                "password": self.bitget_password,
            },
            "bybit": {
                "apiKey": self.bybit_api_key,
                "secret": self.bybit_secret,
            },
            "gateio": {
                "apiKey": self.gateio_api_key,
                "secret": self.gateio_secret,
            },
            "htx": {
                "apiKey": self.htx_api_key,
                "secret": self.htx_secret,
            },
            "kucoin": {
                "apiKey": self.kucoin_api_key,
                "secret": self.kucoin_secret,
                "password": self.kucoin_password,
            },
            "mexc": {
                "apiKey": self.mexc_api_key,
                "secret": self.mexc_secret,
            },
            "okx": {
                "apiKey": self.okx_api_key,
                "secret": self.okx_secret,
                "password": self.okx_password,
            },
            "bitmart": {
                "apiKey": self.bitmart_api_key,
                "secret": self.bitmart_secret,
                "uid": self.bitmart_uid,
            },
            "poloniex": {
                "apiKey": self.poloniex_api_key,
                "secret": self.poloniex_secret,
            },
            "lbank": {
                "apiKey": self.lbank_api_key,
                "secret": self.lbank_secret,
            },
            "coinex": {
                "apiKey": self.coinex_api_key,
                "secret": self.coinex_secret,
            },
            "bingx": {
                "apiKey": self.bingx_api_key,
                "secret": self.bingx_secret,
            },
            "xt": {
                "apiKey": self.xt_api_key,
                "secret": self.xt_secret,
            },
        }
        return configs.get(exchange_id, {})

    def get_active_exchanges(self) -> list[str]:
        exchanges = []
        exchange_keys = {
            "binance": self.binance_api_key,
            "bitget": self.bitget_api_key,
            "bybit": self.bybit_api_key,
            "gateio": self.gateio_api_key,
            "htx": self.htx_api_key,
            "kucoin": self.kucoin_api_key,
            "mexc": self.mexc_api_key,
            "okx": self.okx_api_key,
            "bitmart": self.bitmart_api_key,
            "poloniex": self.poloniex_api_key,
            "lbank": self.lbank_api_key,
            "coinex": self.coinex_api_key,
            "bingx": self.bingx_api_key,
            "xt": self.xt_api_key,
        }
        for exchange_id, api_key in exchange_keys.items():
            if api_key:
                exchanges.append(exchange_id)
        return exchanges


@lru_cache
def get_settings() -> Settings:
    return Settings()
