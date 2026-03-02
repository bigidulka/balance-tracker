import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services import transaction_service
from app.services.transaction_service import (
    TransactionService,
    _transactions_refresh_inflight,
)


class TransactionRefreshSingleflightCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _transactions_refresh_inflight.clear()

    async def asyncTearDown(self):
        _transactions_refresh_inflight.clear()

    async def test_leader_cancellation_does_not_cancel_shared_refresh_for_follower(self):
        service = TransactionService(session=AsyncMock(), organization_id=1)
        service.entitlements.ensure_refresh_interval_for_organization = AsyncMock(
            return_value=None
        )

        started = asyncio.Event()
        release = asyncio.Event()

        async def _fetch_transactions(*args, **kwargs):
            started.set()
            await release.wait()
            return {}

        with patch.object(transaction_service.settings, "enable_ccxt_singleflight", True):
            with patch.object(
                transaction_service.ccxt_manager,
                "fetch_transactions_all_exchanges",
                AsyncMock(side_effect=_fetch_transactions),
            ) as fetch_mock:
                leader = asyncio.create_task(
                    service.refresh_transactions(exchange_ids=["binance"], since_hours=1)
                )
                await asyncio.wait_for(started.wait(), timeout=1)

                follower = asyncio.create_task(
                    service.refresh_transactions(exchange_ids=["binance"], since_hours=1)
                )

                leader.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await leader

                release.set()
                result = await asyncio.wait_for(follower, timeout=1)

        self.assertEqual(fetch_mock.await_count, 1)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.new_transactions, 0)
        self.assertEqual(result.updated_transactions, 0)

        for _ in range(20):
            if not _transactions_refresh_inflight:
                break
            await asyncio.sleep(0)
        self.assertFalse(_transactions_refresh_inflight)
