# Exchange REST Migration Notes

## Goal

Prepare `balance-tracker` for a staged migration from `ccxt` to official exchange REST APIs without changing the external `BalanceService` contract.

## Current rollout shape

- `CCXTManager.fetch_balance()` is now a transport entrypoint, not a hardwired CCXT-only implementation.
- The actual CCXT path lives in `CCXTManager._fetch_balance_via_ccxt()`.
- Gateway selection is handled by `app/services/exchange_rest.py`.
- Default behavior remains `ccxt`.
- REST mode is currently implemented for every configured CEX in the project:
  `binance`, `okx`, `bybit`, `bitget`, `gateio`, `htx`, `kucoin`, `mexc`, `bitmart`, `poloniex`, `lbank`, `coinex`, `bingx`, `xt`.
- Unsupported REST overrides automatically fall back to `ccxt`.

## Config

- `EXCHANGE_DEFAULT_TRANSPORT=ccxt`
- `EXCHANGE_TRANSPORT_OVERRIDES={}`

Examples:

```dotenv
EXCHANGE_DEFAULT_TRANSPORT=ccxt
EXCHANGE_TRANSPORT_OVERRIDES={"binance":"rest","okx":"rest"}
```

## Official REST references used for preparation

- Binance Spot Account: [developers.binance.com/docs/binance-spot-api-docs/rest-api/account-endpoints#account-information-user_data](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/account-endpoints#account-information-user_data)
- Binance Funding Wallet: [developers.binance.com/docs/wallet/asset/funding-wallet](https://developers.binance.com/docs/wallet/asset/funding-wallet)
- Binance Margin Account: [developers.binance.com/docs/margin_trading/account/Query-Cross-Margin-Account-Details](https://developers.binance.com/docs/margin_trading/account/Query-Cross-Margin-Account-Details)
- Binance USD-M Futures Account: [developers.binance.com/docs/derivatives/usds-margined-futures/account/rest-api/Account-Information-V3](https://developers.binance.com/docs/derivatives/usds-margined-futures/account/rest-api/Account-Information-V3)
- Binance COIN-M Futures Balance: [developers.binance.com/docs/derivatives/coin-margined-futures/account/rest-api/Futures-Account-Balance](https://developers.binance.com/docs/derivatives/coin-margined-futures/account/rest-api/Futures-Account-Balance)
- OKX Trading Balance: [my.okx.com/docs-v5/en/#trading-account-rest-api-get-balance](https://my.okx.com/docs-v5/en/#trading-account-rest-api-get-balance)
- OKX Funding Balance: [my.okx.com/docs-v5/en/#funding-account-rest-api-get-balance](https://my.okx.com/docs-v5/en/#funding-account-rest-api-get-balance)
- OKX REST Authentication: [my.okx.com/docs-v5/en/#overview-rest-authentication](https://my.okx.com/docs-v5/en/#overview-rest-authentication)

## Next migration slices

1. Move deposits/withdrawals behind the same gateway interface.
2. Split ticker pricing from transport selection into a dedicated market-data client.
3. Add exchange-specific response fixtures for REST parsing.
4. Add parity checks against current `ccxt` payloads on real accounts.
5. When a REST gateway reaches parity, flip its entry in `EXCHANGE_TRANSPORT_OVERRIDES`.
