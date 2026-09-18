#!/usr/bin/env python3
"""Offline demo: synthetic balances -> SQLite -> bot aggregation.

No network, no exchange credentials, no PostgreSQL. The script points the application at a
throwaway SQLite file, runs the real schema creation, writes synthetic CEX/DEX rows through
`BalanceRepository` and then renders the same summary the Telegram UI builds with
`bot.services.balance.parse_balances`.

Executed for real: schema creation, repository persistence, service-key normalization and
the bot's aggregation/reporting path.
Synthetic: the balance payloads themselves (no exchange was queried).

Usage:
    python scripts/demo_sqlite.py
    python scripts/demo_sqlite.py --json-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "tmp" / "demo-portfolio.db"
OPERATOR_WALLET = "0xf9095877f93603d0b6c44e5a82db5dc751b34cd8"
TRON_WALLET = "TAAJTevA5fFsuizex43KQX2GnFCrDYoHVH"


def _prepare_database_file(db_path: Path) -> None:
    """Create a fresh SQLite file; the demo never touches the configured application database."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()


async def _seed(session: Any) -> tuple[int, list[Any]]:
    from app.models.balance import Integration, Organization
    from app.repositories.balance import BalanceRepository
    from app.schemas.balance import AccountBalanceSchema, AssetSchema, ServiceBalanceSchema

    organization = Organization(name="Demo Organization", slug="demo-organization")
    session.add(organization)
    await session.flush()

    integrations = [
        Integration(
            organization_id=organization.id,
            provider="ccxt",
            kind="cex",
            exchange_code="binance",
            account_ref="main",
            name="Binance main",
            is_active=True,
        ),
        Integration(
            organization_id=organization.id,
            provider="okx_wallet",
            kind="dex",
            wallet_address=OPERATOR_WALLET,
            chain="ethereum",
            name="EVM wallet",
            is_active=True,
        ),
        Integration(
            organization_id=organization.id,
            provider="tron_ton",
            kind="dex",
            wallet_address=TRON_WALLET,
            chain="tron",
            name="TRON wallet",
            is_active=True,
        ),
    ]
    session.add_all(integrations)
    await session.flush()

    repository = BalanceRepository(session)
    now = datetime.now(timezone.utc)

    exchange_assets = [
        AssetSchema(coin="USDT", amount=8200.0, value_usd=8200.0),
        AssetSchema(coin="BTC", amount=0.12, value_usd=7800.0),
    ]
    await repository.save_balance(
        "binance",
        exchange_assets,
        sum(asset.value_usd for asset in exchange_assets),
        accounts=[
            AccountBalanceSchema(account_type="spot", assets=exchange_assets, total_usd=16000.0),
            AccountBalanceSchema(
                account_type="futures",
                assets=[AssetSchema(coin="USDT", amount=1450.0, value_usd=1450.0)],
                total_usd=1450.0,
            ),
        ],
        organization_id=organization.id,
        integration_id=integrations[0].id,
    )
    await repository.save_balance(
        "okx_wallet_" + OPERATOR_WALLET.lower(),
        [AssetSchema(coin="ETH", amount=1.4, value_usd=4200.0)],
        4200.0,
        organization_id=organization.id,
        integration_id=integrations[1].id,
    )
    # Legacy TRON key: the parser is expected to normalize it to the canonical wallet entry.
    await repository.save_balance(
        "tron_ton_" + TRON_WALLET.lower(),
        [AssetSchema(coin="TRX", amount=12500.0, value_usd=2500.0)],
        2500.0,
        organization_id=organization.id,
        integration_id=integrations[2].id,
    )

    await session.commit()

    rows = await repository.get_all_latest_balances(organization.id)
    payload = {
        "services": [
            {
                "service": row.service,
                "integration_id": row.integration_id,
                "assets": row.assets or [],
                "accounts": row.accounts or [],
                "total_usd": row.total_usd,
                "actual": row.actual,
                "updated_at": now.isoformat(),
            }
            for row in rows
        ]
    }
    return organization.id, payload


async def run_demo(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    _prepare_database_file(db_path)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import app.models  # noqa: F401  (import registers every ORM table on Base.metadata)
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.database import Base
    from bot.services.balance import parse_balances

    # Dedicated engine: the demo is independent from the application's configured database.
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            organization_id, payload = await _seed(session)
    finally:
        await engine.dispose()

    summary = parse_balances(payload)
    summary["organization_id"] = organization_id
    summary["database"] = str(db_path)
    return summary


def _print_report(summary: dict[str, Any]) -> None:
    print("Offline demo — synthetic balances in a throwaway SQLite database, no exchange calls.")
    print(f"database: {summary['database']}")
    print()
    print(f"  total          : ${summary['total']:,.2f}")
    print(f"  spot           : ${summary['spot_total']:,.2f}")
    print(f"  futures        : ${summary['futures_total']:,.2f}")
    print(f"  dex            : ${summary['dex_total']:,.2f}")
    print(f"  exchanges      : {summary['exchanges_count']}")
    print(f"  dex wallets    : {len(summary['dex_wallets'])}")
    for key, wallet in summary["dex_wallets"].items():
        print(f"    - {key}: ${wallet.get('total_usd', 0):,.2f}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline SQLite demo with synthetic balances.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite file to create")
    parser.add_argument("--json-only", action="store_true", help="print only the summary JSON")
    arguments = parser.parse_args(argv)

    summary = asyncio.run(run_demo(arguments.db))
    if not arguments.json_only:
        _print_report(summary)
    printable = {key: value for key, value in summary.items() if key != "database"}
    print(json.dumps(printable, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
