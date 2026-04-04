import unittest
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.repositories.balance import BalanceRepository, TransactionRepository
from app.schemas.balance import AssetSchema, TransactionSchema


class TenantIsolationRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_balance_repository_scopes_latest_balances_by_org(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)

            await repo.save_balance(
                service="binance",
                assets=[AssetSchema(coin="USDT", amount=10, value_usd=10)],
                total_usd=10,
                organization_id=1,
            )
            await repo.save_balance(
                service="binance",
                assets=[AssetSchema(coin="USDT", amount=20, value_usd=20)],
                total_usd=20,
                organization_id=2,
            )

            org1 = await repo.get_latest_balance("binance", organization_id=1)
            org2 = await repo.get_latest_balance("binance", organization_id=2)

            self.assertIsNotNone(org1)
            self.assertIsNotNone(org2)
            self.assertEqual(org1.total_usd, 10)
            self.assertEqual(org2.total_usd, 20)

    async def test_transaction_repository_scopes_reads_and_counts_by_org(self):
        async with self.session_maker() as session:
            repo = TransactionRepository(session)

            tx1 = TransactionSchema(
                tx_id="tx-1",
                service="binance",
                tx_type="deposit",
                currency="USDT",
                amount=100,
                status="ok",
                tx_timestamp=datetime.now(timezone.utc),
            )
            tx2 = TransactionSchema(
                tx_id="tx-2",
                service="binance",
                tx_type="deposit",
                currency="USDT",
                amount=200,
                status="ok",
                tx_timestamp=datetime.now(timezone.utc),
            )

            await repo.save_transaction(tx1, organization_id=1)
            await repo.save_transaction(tx2, organization_id=2)

            org1_transactions = await repo.get_transactions(organization_id=1)
            org2_transactions = await repo.get_transactions(organization_id=2)
            org1_count = await repo.get_transaction_count(organization_id=1)
            org2_count = await repo.get_transaction_count(organization_id=2)

            self.assertEqual(len(org1_transactions), 1)
            self.assertEqual(len(org2_transactions), 1)
            self.assertEqual(org1_transactions[0].tx_id, "tx-1")
            self.assertEqual(org2_transactions[0].tx_id, "tx-2")
            self.assertEqual(org1_count, 1)
            self.assertEqual(org2_count, 1)
