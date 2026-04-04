import unittest
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.repositories.balance import BalanceRepository
from app.schemas.balance import AccountBalanceSchema, AssetSchema
from app.services.balance_service import BalanceService


class BalanceIntegrityHotfixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_save_balance_persists_positive_to_zero_transition(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)

            await repo.save_balance(
                service="binance",
                assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                total_usd=100,
                actual=True,
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                        total_usd=100,
                    )
                ],
                organization_id=1,
            )

            latest = await repo.save_balance(
                service="binance",
                assets=[],
                total_usd=0,
                actual=True,
                accounts=[],
                organization_id=1,
            )

            history = await repo.get_history(organization_id=1, service="binance", limit=10)

        self.assertEqual(latest.total_usd, 0)
        self.assertTrue(latest.actual)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].total_usd, 0)

    async def test_save_balance_tracks_accounts_change_even_without_total_change(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)
            await repo.save_balance(
                service="binance",
                assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                total_usd=100,
                actual=True,
                accounts=[
                    AccountBalanceSchema(
                        account_type="spot",
                        assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                        total_usd=100,
                    )
                ],
                organization_id=1,
            )

            await repo.save_balance(
                service="binance",
                assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                total_usd=100,
                actual=True,
                accounts=[
                    AccountBalanceSchema(
                        account_type="futures",
                        assets=[AssetSchema(coin="USDT", amount=100, value_usd=100)],
                        total_usd=100,
                    )
                ],
                organization_id=1,
            )

            history = await repo.get_history(organization_id=1, service="binance", limit=10)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].accounts[0]["account_type"], "futures")

    async def test_balance_service_fallback_returns_known_zero_balance(self):
        async with self.session_maker() as session:
            repo = BalanceRepository(session)
            await repo.save_balance(
                service="binance",
                assets=[],
                total_usd=0,
                actual=True,
                accounts=[],
                organization_id=1,
            )

            service = BalanceService(session=session, organization_id=1)
            fallback = await service._get_fallback_balance("binance")

        self.assertIsNotNone(fallback)
        self.assertEqual(fallback.total_usd, 0)
        self.assertFalse(fallback.actual)
