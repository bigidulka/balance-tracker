import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.balance import Integration, Organization, ServiceStatus, Transaction
from app.schemas.balance import TransactionSchema
from app.services.transaction_service import TransactionService


class DexTransactionRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_refresh_polls_healthy_evm_wallet_transactions(self):
        async with self.session_maker() as session:
            org = Organization(name="Org", slug="org")
            session.add(org)
            await session.flush()
            wallet = "0x1111111111111111111111111111111111111111"
            integration = Integration(
                organization_id=org.id,
                provider="okx_wallet",
                name="Wallet",
                kind="dex",
                wallet_address=wallet,
                chain="eth",
                is_active=True,
            )
            service_key = f"evm_{wallet.lower()}"
            session.add_all(
                [
                    integration,
                    ServiceStatus(
                        organization_id=org.id,
                        service=service_key,
                        is_healthy=True,
                    ),
                ]
            )
            await session.commit()

            tx = TransactionSchema(
                tx_id=f"{service_key}:hash:0",
                service=service_key,
                tx_type="deposit",
                currency="USDC",
                amount=10,
                status="ok",
                tx_timestamp=datetime.now(timezone.utc),
            )
            service = TransactionService(session, organization_id=org.id)
            service.entitlements.ensure_refresh_interval_for_organization = AsyncMock(return_value=None)

            with patch(
                "app.services.transaction_service.debank_sdk_client.fetch_wallet_transactions",
                AsyncMock(return_value=[tx]),
            ) as fetch_mock:
                result = await service.refresh_transactions(since_hours=1)

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.new_transactions, 1)
            self.assertEqual(result.failed_services, [])
            fetch_mock.assert_awaited_once()

            rows = await service.tx_repo.get_transactions(organization_id=org.id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].service, service_key)
            self.assertEqual(rows[0].integration_id, integration.id)

    async def test_refresh_routes_sui_tron_ton_wallet_transactions(self):
        async with self.session_maker() as session:
            org = Organization(name="Org", slug="org")
            session.add(org)
            await session.flush()
            sui_wallet = "0x" + "3" * 64
            tron_wallet = "T" + "A" * 33
            ton_wallet = "UQ" + "A" * 46
            targets = [
                ("sui", "sui", sui_wallet, f"sui_{sui_wallet.lower()}"),
                ("tron_ton", "tron", tron_wallet, f"tron_{tron_wallet.lower()}"),
                ("tron_ton", "ton", ton_wallet, f"ton_{ton_wallet.lower()}"),
            ]
            for provider, chain, wallet, service_key in targets:
                session.add(
                    Integration(
                        organization_id=org.id,
                        provider=provider,
                        name=f"{chain} wallet",
                        kind="dex",
                        wallet_address=wallet,
                        chain=chain,
                        is_active=True,
                    )
                )
                session.add(
                    ServiceStatus(
                        organization_id=org.id,
                        service=service_key,
                        is_healthy=True,
                    )
                )
            await session.commit()

            service = TransactionService(session, organization_id=org.id)
            service.entitlements.ensure_refresh_interval_for_organization = AsyncMock(return_value=None)

            with patch(
                "app.services.transaction_service.sui_service.fetch_wallet_transactions",
                AsyncMock(return_value=[]),
            ) as sui_mock, patch(
                "app.services.transaction_service.tron_ton_service.fetch_wallet_transactions",
                AsyncMock(return_value=[]),
            ) as tron_ton_mock:
                result = await service.refresh_transactions(since_hours=1)

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.failed_services, [])
            self.assertEqual(sui_mock.await_count, 1)
            self.assertEqual(tron_ton_mock.await_count, 2)
            self.assertEqual(set(result.services_checked), {item[3] for item in targets})

    async def test_refresh_skips_unhealthy_dex_wallet_transactions(self):
        async with self.session_maker() as session:
            org = Organization(name="Org", slug="org")
            session.add(org)
            await session.flush()
            wallet = "0x2222222222222222222222222222222222222222"
            service_key = f"evm_{wallet.lower()}"
            session.add_all(
                [
                    Integration(
                        organization_id=org.id,
                        provider="okx_wallet",
                        name="Wallet",
                        kind="dex",
                        wallet_address=wallet,
                        chain="eth",
                        is_active=True,
                    ),
                    ServiceStatus(
                        organization_id=org.id,
                        service=service_key,
                        is_healthy=False,
                    ),
                ]
            )
            await session.commit()

            service = TransactionService(session, organization_id=org.id)
            service.entitlements.ensure_refresh_interval_for_organization = AsyncMock(return_value=None)

            with patch(
                "app.services.transaction_service.debank_sdk_client.fetch_wallet_transactions",
                AsyncMock(return_value=[]),
            ) as fetch_mock:
                result = await service.refresh_transactions(since_hours=1)

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.new_transactions, 0)
            fetch_mock.assert_not_awaited()
