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
    database_url: str = Field(default="")
    database_use_sqlite: bool = Field(default=False)
    sqlite_database_url: str = Field(default="***REMOVED***")
    postgres_host: str = Field(default="postgres")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="balance_tracker")
    postgres_user: str = Field(default="balance_tracker")
    postgres_password: str = Field(default="balance_tracker")
    db_pool_size: int = Field(default=10)
    db_max_overflow: int = Field(default=20)
    db_pool_timeout: int = Field(default=30)
    db_pool_recycle: int = Field(default=1800)

    cors_allow_origins: str = Field(default='["http://localhost:3000"]')
    default_org_id: int = Field(default=1)

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
    balance_cache_hard_ttl: int = Field(default=120)
    request_timeout: int = Field(default=30)
    ccxt_balance_call_timeout_seconds: float = Field(default=30.0)
    ccxt_inter_account_delay_seconds: float = Field(default=0.0)
    ccxt_inter_exchange_delay_seconds: float = Field(default=0.0)
    ccxt_max_exchanges_per_cycle: int = Field(default=0)
    ccxt_keyed_parallelism: int = Field(default=2)
    ccxt_backpressure_wait_timeout_seconds: float = Field(default=2.0)
    transactions_singleflight_since_bucket_seconds: int = Field(default=60)
    integration_secret_key: str = Field(default="")

    enable_inprocess_refresh_loop: bool = Field(default=True)
    enable_worker: bool = Field(default=False)
    enable_ccxt_singleflight: bool = Field(default=False)
    enable_ccxt_stale_revalidate: bool = Field(default=False)
    enable_ccxt_keyed_backpressure: bool = Field(default=False)
    enable_syncjob_dedupe: bool = Field(default=False)
    enable_shared_cache_l2: bool = Field(default=False)
    exchange_parallelism: int = Field(default=8)
    job_parallelism: int = Field(default=2)

    # Legacy aliases kept for backward compatibility during rollout.
    enable_legacy_background_refresh_loop: Optional[bool] = Field(default=None)
    enable_inprocess_sync_worker: Optional[bool] = Field(default=None)
    sync_worker_poll_interval_seconds: float = Field(default=2.0)

    jwt_secret_key: str = Field(default="dev-change-me")
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_token_expire_minutes: int = Field(default=60 * 24)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if self.database_use_sqlite:
            return self.sqlite_database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def legacy_background_refresh_loop_enabled(self) -> bool:
        if self.enable_legacy_background_refresh_loop is not None:
            return self.enable_legacy_background_refresh_loop
        return self.enable_inprocess_refresh_loop

    @property
    def inprocess_sync_worker_enabled(self) -> bool:
        if self.enable_inprocess_sync_worker is not None:
            return self.enable_inprocess_sync_worker
        return self.enable_worker

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

    @property
    def cors_origins(self) -> list[str]:
        try:
            origins = json.loads(self.cors_allow_origins)
            return [o for o in origins if isinstance(o, str)]
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
