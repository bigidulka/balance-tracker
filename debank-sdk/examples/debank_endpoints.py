import json
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

API_BASE_URL = "https://api.debank.com"
ROOT = Path(__file__).resolve().parents[1]
SIGNER = ROOT / "examples" / "debank_sign_headers.js"


def build_headers(path: str, params: dict[str, Any]) -> dict[str, str]:
    payload = json.dumps({"path": path, "params": params}, ensure_ascii=False)
    raw = subprocess.check_output(
        ["node", str(SIGNER), payload],
        cwd=ROOT,
        text=True,
    )
    return json.loads(raw)


def get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    last_error: Exception | None = None

    for attempt in range(4):
        headers = build_headers(path, params)
        response = requests.get(
            f"{API_BASE_URL}{path}",
            params=params,
            headers=headers,
            timeout=30,
        )

        if response.status_code != 429:
            response.raise_for_status()
            return response.json()

        last_error = requests.HTTPError(
            f"429 Too Many Requests for {path}",
            response=response,
        )
        time.sleep(2 * (attempt + 1))

    if last_error:
        raise last_error
    raise RuntimeError(f"Request failed for {path}")


def get_total_net_curve(user_addr: str, days: int = 1) -> dict[str, Any]:
    return get_json("/asset/total_net_curve", {"user_addr": user_addr, "days": days})


def get_used_chains(wallet: str) -> dict[str, Any]:
    return get_json("/user/used_chains", {"id": wallet})


def get_token_cache_balance_list(user_addr: str) -> dict[str, Any]:
    return get_json("/token/cache_balance_list", {"user_addr": user_addr})


def get_token_balance_list(user_addr: str, chain: str) -> dict[str, Any]:
    return get_json("/token/balance_list", {"user_addr": user_addr, "chain": chain})


def get_portfolio_app_list(user_id: str) -> dict[str, Any]:
    return get_json("/portfolio/app_list", {"user_id": user_id})


def get_portfolio_project_list(user_addr: str) -> dict[str, Any]:
    return get_json("/portfolio/project_list", {"user_addr": user_addr})


def get_history_list(
    user_addr: str,
    chain: str = "",
    start_time: int = 0,
    page_count: int = 20,
) -> dict[str, Any]:
    return get_json(
        "/history/list",
        {
            "user_addr": user_addr,
            "chain": chain,
            "start_time": start_time,
            "page_count": page_count,
        },
    )


def get_chain_list() -> dict[str, Any]:
    return get_json("/chain/list", {})


def main() -> None:
    wallet = "0x3ec68709334f64ee4927891627f0b395c6ff6754"
    results: dict[str, Any] = {}

    results["chain_list"] = get_chain_list()
    print("chain_list ok")

    results["total_net_curve"] = get_total_net_curve(wallet, days=1)
    print("total_net_curve ok")

    results["used_chains"] = get_used_chains(wallet)
    print("used_chains ok")

    results["token_cache_balance_list"] = get_token_cache_balance_list(wallet)
    print("token_cache_balance_list ok")

    chains = (((results["used_chains"].get("data") or {}).get("chains")) or [])
    first_chain = chains[0] if chains else "eth"

    results["token_balance_list"] = get_token_balance_list(wallet, first_chain)
    print("token_balance_list ok")

    results["portfolio_app_list"] = get_portfolio_app_list(wallet)
    print("portfolio_app_list ok")

    results["portfolio_project_list"] = get_portfolio_project_list(wallet)
    print("portfolio_project_list ok")

    results["history_list"] = get_history_list(wallet, chain="", start_time=0, page_count=20)
    print("history_list ok")

    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
