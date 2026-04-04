import asyncio
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
KEYS_PATH = Path(r"C:\perenos\my_projects\ARBITRON_PROJECT\_configs\api_keys.json")
OUTPUT_PATH = ROOT / "data" / "ccxt_rest_comparison.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


EXCHANGE_ENV_MAP = {
    "binance_keys": ("BINANCE_API_KEY", "BINANCE_SECRET", None, None),
    "bitget_keys": ("BITGET_API_KEY", "BITGET_SECRET", "BITGET_PASSWORD", None),
    "bybit_keys": ("BYBIT_API_KEY", "BYBIT_SECRET", None, None),
    "gateio_keys": ("GATEIO_API_KEY", "GATEIO_SECRET", None, None),
    "huobi_keys": ("HTX_API_KEY", "HTX_SECRET", None, None),
    "kucoin_keys": ("KUCOIN_API_KEY", "KUCOIN_SECRET", "KUCOIN_PASSWORD", None),
    "mexc_keys": ("MEXC_API_KEY", "MEXC_SECRET", None, None),
    "okx_keys": ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSWORD", None),
    "bitmart_keys": ("BITMART_API_KEY", "BITMART_SECRET", None, "BITMART_UID"),
    "poloniex_keys": ("POLONIEX_API_KEY", "POLONIEX_SECRET", None, None),
    "lbank_keys": ("LBANK_API_KEY", "LBANK_SECRET", None, None),
    "coinex_keys": ("COINEX_API_KEY", "COINEX_SECRET", None, None),
    "bingx_keys": ("BINGX_API_KEY", "BINGX_SECRET", None, None),
    "xt_keys": ("XT_API_KEY", "XT_SECRET", None, None),
}


EXCHANGE_ID_MAP = {
    "binance_keys": "binance",
    "bitget_keys": "bitget",
    "bybit_keys": "bybit",
    "gateio_keys": "gateio",
    "huobi_keys": "htx",
    "kucoin_keys": "kucoin",
    "mexc_keys": "mexc",
    "okx_keys": "okx",
    "bitmart_keys": "bitmart",
    "poloniex_keys": "poloniex",
    "lbank_keys": "lbank",
    "coinex_keys": "coinex",
    "bingx_keys": "bingx",
    "xt_keys": "xt",
}


@dataclass
class BalanceSummary:
    status: str
    total_usd: float | None
    asset_count: int
    top_assets: list[dict[str, Any]]
    error: str | None = None


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 8)


def _load_keys() -> list[str]:
    raw = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    proxy = raw.get("proxy_keys", {})
    if proxy:
        os.environ["PROXY_HOST"] = str(proxy.get("host", ""))
        os.environ["PROXY_PORT"] = str(proxy.get("port", ""))
        os.environ["PROXY_USERNAME"] = str(proxy.get("username", ""))
        os.environ["PROXY_PASSWORD"] = str(proxy.get("password", ""))

    active: list[str] = []
    for json_key, exchange_id in EXCHANGE_ID_MAP.items():
        payload = raw.get(json_key) or {}
        if not payload:
            continue
        env_map = EXCHANGE_ENV_MAP[json_key]
        os.environ[env_map[0]] = str(payload.get("apiKey", ""))
        os.environ[env_map[1]] = str(payload.get("secret", ""))
        if env_map[2]:
            os.environ[env_map[2]] = str(payload.get("password", ""))
        if env_map[3]:
            os.environ[env_map[3]] = str(payload.get("uid", ""))
        if payload.get("apiKey"):
            active.append(exchange_id)

    os.environ.setdefault("DATABASE_URL", "***REMOVED***")
    return active


def _summarize_balance(balance) -> BalanceSummary:
    assets = sorted(balance.assets, key=lambda x: x.value_usd, reverse=True)
    return BalanceSummary(
        status="ok",
        total_usd=_round(balance.total_usd),
        asset_count=len(assets),
        top_assets=[
            {
                "coin": asset.coin,
                "amount": _round(asset.amount),
                "value_usd": _round(asset.value_usd),
            }
            for asset in assets[:10]
        ],
    )


def _summarize_error(exc: BaseException) -> BalanceSummary:
    return BalanceSummary(
        status="error",
        total_usd=None,
        asset_count=0,
        top_assets=[],
        error=f"{type(exc).__name__}: {exc}",
    )


def _asset_map(summary: BalanceSummary) -> dict[str, float]:
    return {item["coin"]: float(item["value_usd"] or 0) for item in summary.top_assets}


def _compare_summaries(ccxt_summary: BalanceSummary, rest_summary: BalanceSummary) -> dict[str, Any]:
    if ccxt_summary.status != "ok" or rest_summary.status != "ok":
        return {
            "match": False,
            "reason": "transport_error",
            "ccxt_status": ccxt_summary.status,
            "rest_status": rest_summary.status,
        }

    ccxt_total = float(ccxt_summary.total_usd or 0)
    rest_total = float(rest_summary.total_usd or 0)
    total_delta = round(rest_total - ccxt_total, 8)
    total_delta_pct = None if ccxt_total == 0 else round((total_delta / ccxt_total) * 100, 6)

    ccxt_assets = _asset_map(ccxt_summary)
    rest_assets = _asset_map(rest_summary)
    all_coins = sorted(set(ccxt_assets) | set(rest_assets))
    asset_deltas = []
    for coin in all_coins:
        c_val = round(ccxt_assets.get(coin, 0.0), 8)
        r_val = round(rest_assets.get(coin, 0.0), 8)
        if abs(c_val - r_val) > 0.01:
            asset_deltas.append({"coin": coin, "ccxt_value_usd": c_val, "rest_value_usd": r_val})

    return {
        "match": abs(total_delta) <= 1.0 and not asset_deltas,
        "reason": "ok" if abs(total_delta) <= 1.0 and not asset_deltas else "delta_detected",
        "total_delta_usd": total_delta,
        "total_delta_pct": total_delta_pct,
        "asset_deltas": asset_deltas[:20],
    }


async def _run() -> int:
    active_exchanges = _load_keys()
    from app.services.ccxt_manager import CCXTManager
    from app.services.exchange_rest import ExchangeBalanceGatewayRegistry
    from app.core.config import Settings

    settings = Settings()
    registry = ExchangeBalanceGatewayRegistry(settings)
    manager = CCXTManager()

    results: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "keys_path": str(KEYS_PATH),
        "active_exchanges": active_exchanges,
        "results": {},
    }

    for exchange_id in active_exchanges:
        print(f"[compare] {exchange_id}", flush=True)
        ccxt_summary: BalanceSummary
        rest_summary: BalanceSummary

        try:
            ccxt_balance = await asyncio.wait_for(
                manager._fetch_balance_via_ccxt(exchange_id), timeout=90
            )
            ccxt_summary = _summarize_balance(ccxt_balance)
        except BaseException as exc:
            ccxt_summary = _summarize_error(exc)

        try:
            gateway = registry.resolve_rest(exchange_id)
            rest_balance = await asyncio.wait_for(
                gateway.fetch_balance(exchange_id, manager), timeout=90
            )
            rest_summary = _summarize_balance(rest_balance)
        except BaseException as exc:
            rest_summary = _summarize_error(exc)

        results["results"][exchange_id] = {
            "ccxt": asdict(ccxt_summary),
            "rest": asdict(rest_summary),
            "comparison": _compare_summaries(ccxt_summary, rest_summary),
        }

    await manager.close_all()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    mismatches = [
        exchange_id
        for exchange_id, payload in results["results"].items()
        if not payload["comparison"]["match"]
    ]
    print(f"[compare] wrote {OUTPUT_PATH}")
    print(f"[compare] mismatches={mismatches}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_run()))
    except KeyboardInterrupt:
        raise SystemExit(130)
