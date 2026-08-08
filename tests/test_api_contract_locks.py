import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.balance import (
    Balance,
    BalanceHistory,
    Integration,
    Organization,
    ServiceStatus,
    SyncJob,
    Transaction,
)
from app.routers.balances import (
    _day_start_for_offset,
    get_dashboard_summary,
    get_history,
    get_history_chart,
)
from app.routers.integrations import (
    activate_integration,
    deactivate_integration,
    list_integrations,
    refresh_integration,
)


class DashboardSummaryContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_dashboard_summary_response_shape_is_stable(self):
        async with self.session_maker() as session:
            org = Organization(name="Dashboard Org", slug="dashboard-org")
            session.add(org)
            await session.flush()

            now = datetime.now(timezone.utc)
            session.add_all(
                [
                    Balance(
                        organization_id=org.id,
                        service="binance",
                        assets=[],
                        accounts=[{"account_type": "spot", "assets": [], "total_usd": 100.0}],
                        total_usd=100.0,
                        actual=True,
                        updated_at=now,
                    ),
                    Balance(
                        organization_id=org.id,
                        service="okx_wallet_demoabcd",
                        assets=[],
                        accounts=[],
                        total_usd=50.0,
                        actual=True,
                        updated_at=now,
                    ),
                    Integration(
                        organization_id=org.id,
                        provider="binance",
                        name="Binance Main",
                        kind="cex",
                        exchange_code="binance",
                        account_ref="main",
                        is_active=True,
                    ),
                    Integration(
                        organization_id=org.id,
                        provider="okx_wallet",
                        name="Wallet Main",
                        kind="dex",
                        wallet_address="demoabcd1234",
                        chain="ethereum",
                        is_active=True,
                    ),
                    Integration(
                        organization_id=org.id,
                        provider="binance",
                        name="Binance Old",
                        kind="cex",
                        exchange_code="binance",
                        account_ref="old",
                        is_active=False,
                        status="inactive",
                    ),
                    Transaction(
                        organization_id=org.id,
                        tx_id="tx-ok",
                        service="binance",
                        tx_type="deposit",
                        currency="USDT",
                        amount=10.0,
                        status="ok",
                        tx_timestamp=now - timedelta(hours=1),
                    ),
                    Transaction(
                        organization_id=org.id,
                        tx_id="tx-pending",
                        service="binance",
                        tx_type="withdrawal",
                        currency="USDT",
                        amount=5.0,
                        status="pending",
                        tx_timestamp=now - timedelta(hours=2),
                    ),
                    Transaction(
                        organization_id=org.id,
                        tx_id="tx-failed",
                        service="binance",
                        tx_type="withdrawal",
                        currency="USDT",
                        amount=3.0,
                        status="failed",
                        tx_timestamp=now - timedelta(hours=3),
                    ),
                ]
            )
            await session.commit()

            response = await get_dashboard_summary(
                db=session,
                organization_id=org.id,
                _=object(),
            )

            payload = response.model_dump()
            self.assertEqual(
                set(payload.keys()),
                {
                    "total_usd",
                    "exchanges_count",
                    "spot_total",
                    "futures_total",
                    "dex_total",
                    "freshness",
                    "latest_updated_at",
                    "plan",
                    "capabilities",
                    "throttling",
                    "integrations",
                    "transactions_24h",
                    "timestamp",
                    # Trader metrics
                    "balance_today_start",
                    "balance_24h_ago",
                    "balance_7d_ago",
                    "balance_30d_ago",
                    "pnl_today",
                    "pnl_today_pct",
                    "pnl_24h",
                    "pnl_24h_pct",
                    "pnl_7d",
                    "pnl_7d_pct",
                    "pnl_30d",
                    "pnl_30d_pct",
                    "pnl_includes_transfers",
                },
            )


            self.assertEqual(set(payload["integrations"].keys()), {"total", "active", "inactive"})
            self.assertEqual(set(payload["transactions_24h"].keys()), {"total", "pending", "failed"})
            self.assertEqual(payload["integrations"]["total"], 3)
            self.assertEqual(payload["integrations"]["active"], 2)
            self.assertEqual(payload["integrations"]["inactive"], 1)
            self.assertEqual(payload["transactions_24h"]["total"], 3)
            self.assertEqual(payload["transactions_24h"]["pending"], 1)
            self.assertEqual(payload["transactions_24h"]["failed"], 1)

            self.assertEqual(payload["total_usd"], 150.0)
            self.assertEqual(payload["exchanges_count"], 1)
            self.assertEqual(payload["spot_total"], 100.0)
            self.assertEqual(payload["futures_total"], 0.0)
            self.assertEqual(payload["dex_total"], 50.0)
            self.assertTrue(payload["freshness"].startswith("updated_") or payload["freshness"] == "updated_just_now")

    async def test_dashboard_pnl_uses_active_key_snapshot_without_short_window(self):
        async with self.session_maker() as session:
            org = Organization(name="Dashboard PnL Org", slug="dashboard-pnl-org")
            session.add(org)
            await session.flush()

            now = datetime.now(timezone.utc)
            day_start = _day_start_for_offset(now, 180)
            old_snapshot_time = day_start - timedelta(hours=18)

            integration = Integration(
                organization_id=org.id,
                provider="ccxt",
                name="Gate.io Main",
                kind="cex",
                exchange_code="gateio",
                account_ref="main",
                is_active=True,
                status="active",
            )
            session.add(integration)
            await session.flush()
            session.add_all(
                [
                    Balance(
                        organization_id=org.id,
                        integration_id=integration.id,
                        service="gateio",
                        assets=[],
                        accounts=[],
                        total_usd=370.0,
                        actual=True,
                        updated_at=now,
                    ),
                    BalanceHistory(
                        organization_id=org.id,
                        integration_id=integration.id,
                        service="gateio",
                        assets=[],
                        accounts=[],
                        total_usd=365.0,
                        actual=True,
                        created_at=old_snapshot_time,
                    ),
                ]
            )
            await session.commit()

            response = await get_dashboard_summary(
                include_metrics=True,
                utc_offset_minutes=180,
                db=session,
                organization_id=org.id,
                _=object(),
            )

            payload = response.model_dump()
            self.assertEqual(payload["balance_today_start"], 365.0)
            self.assertEqual(payload["pnl_today"], 5.0)


class IntegrationLifecycleContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _identity(org_id: int, user_id: int = 1001) -> SimpleNamespace:
        return SimpleNamespace(
            organization=SimpleNamespace(id=org_id),
            user=SimpleNamespace(id=user_id),
        )

    @staticmethod
    def _integration_response_keys() -> set[str]:
        return {
            "id",
            "provider",
            "name",
            "kind",
            "status",
            "exchange_code",
            "account_ref",
            "wallet_address",
            "chain",
            "is_active",
            "last_synced_at",
            "is_healthy",
            "health_status",
            "last_error",
            "last_check",
            "last_job_status",
            "last_job_error",
            "last_job_finished_at",
            "created_at",
            "updated_at",
        }

    async def test_integration_lifecycle_endpoint_shapes_are_stable(self):
        async with self.session_maker() as session:
            org = Organization(name="Lifecycle Contract Org", slug="lifecycle-contract-org")
            session.add(org)
            await session.flush()

            integration = Integration(
                organization_id=org.id,
                provider="binance",
                name="Contract Binance",
                kind="cex",
                exchange_code="binance",
                account_ref="main",
                is_active=True,
                status="active",
            )
            session.add(integration)
            await session.commit()
            await session.refresh(integration)

            identity = self._identity(org.id)

            deactivated = await deactivate_integration(
                integration_id=integration.id,
                db=session,
                identity=identity,
            )
            deactivated_payload = deactivated.model_dump()
            self.assertEqual(
                set(deactivated_payload.keys()), self._integration_response_keys()
            )
            self.assertFalse(deactivated.is_active)

            activated = await activate_integration(
                integration_id=integration.id,
                db=session,
                identity=identity,
            )
            activated_payload = activated.model_dump()
            self.assertEqual(set(activated_payload.keys()), set(deactivated_payload.keys()))
            self.assertTrue(activated.is_active)

            refresh = await refresh_integration(
                integration_id=integration.id,
                db=session,
                identity=identity,
            )
            refresh_payload = refresh.model_dump()
            self.assertEqual(
                set(refresh_payload.keys()),
                {"status", "message", "integration_id", "job_id", "job_status"},
            )
            self.assertEqual(refresh.status, "queued")
            self.assertEqual(refresh.integration_id, integration.id)
            self.assertGreater(refresh.job_id, 0)
            self.assertIn(refresh.job_status, {"queued", "running"})

    async def test_integration_list_exposes_health_from_status_and_latest_job(self):
        async with self.session_maker() as session:
            org = Organization(name="Health Contract Org", slug="health-contract-org")
            session.add(org)
            await session.flush()

            now = datetime.now(timezone.utc)
            okx = Integration(
                organization_id=org.id,
                provider="ccxt",
                name="OKX main",
                kind="cex",
                exchange_code="okx",
                account_ref="main",
                is_active=True,
                status="active",
            )
            gate = Integration(
                organization_id=org.id,
                provider="ccxt",
                name="Gate.io main",
                kind="cex",
                exchange_code="gateio",
                account_ref="main",
                is_active=True,
                status="active",
                last_synced_at=now - timedelta(minutes=30),
            )
            session.add_all([okx, gate])
            await session.flush()
            session.add_all(
                [
                    ServiceStatus(
                        organization_id=org.id,
                        service="okx",
                        is_healthy=False,
                        last_error="Degraded balance payload",
                    ),
                    ServiceStatus(
                        organization_id=org.id,
                        service="gateio",
                        is_healthy=True,
                    ),
                    SyncJob(
                        organization_id=org.id,
                        integration_id=okx.id,
                        status="failed",
                        error_message="401 Unauthorized",
                        queued_at=now - timedelta(minutes=3),
                        finished_at=now - timedelta(minutes=2),
                    ),
                    SyncJob(
                        organization_id=org.id,
                        integration_id=gate.id,
                        status="failed",
                        error_message="",
                        queued_at=now - timedelta(minutes=3),
                        finished_at=now - timedelta(minutes=2),
                    ),
                ]
            )
            await session.commit()

            response = await list_integrations(
                include_inactive=True,
                db=session,
                identity=self._identity(org.id),
            )
            payload = {item.exchange_code: item.model_dump() for item in response}

            self.assertEqual(
                set(payload["okx"].keys()), self._integration_response_keys()
            )
            self.assertEqual(payload["okx"]["health_status"], "problem")
            self.assertEqual(payload["okx"]["is_healthy"], False)
            self.assertEqual(payload["okx"]["last_job_status"], "failed")
            self.assertIn("401", payload["okx"]["last_error"])

            self.assertEqual(payload["gateio"]["health_status"], "warning")
            self.assertEqual(payload["gateio"]["is_healthy"], True)
            self.assertEqual(payload["gateio"]["last_job_status"], "failed")


class HistoryContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_history_response_shape_is_extended_but_stable(self):
        async with self.session_maker() as session:
            org = Organization(name="History Org", slug="history-org")
            session.add(org)
            await session.flush()

            now = datetime.now(timezone.utc)
            session.add_all(
                [
                    BalanceHistory(
                        organization_id=org.id,
                        service="binance",
                        assets=[{"coin": "USDT", "amount": 100.0, "value_usd": 100.0}],
                        accounts=[{"account_type": "spot", "assets": [], "total_usd": 100.0}],
                        total_usd=100.0,
                        actual=True,
                        created_at=now,
                    ),
                    BalanceHistory(
                        organization_id=org.id,
                        service="binance",
                        assets=[],
                        accounts=[],
                        total_usd=0.0,
                        actual=False,
                        created_at=now + timedelta(hours=1),
                    ),
                ]
            )
            await session.commit()

            response = await get_history(
                service="binance",
                start_date=None,
                end_date=None,
                limit=100,
                db=session,
                organization_id=org.id,
                _=object(),
            )

            payload = response.model_dump()
            self.assertEqual(set(payload.keys()), {"service", "entries", "total_entries"})
            self.assertEqual(payload["total_entries"], 2)
            self.assertEqual(
                set(payload["entries"][0].keys()),
                {"service", "total_usd", "assets", "accounts", "actual", "created_at"},
            )

    async def test_history_chart_response_shape_is_stable(self):
        async with self.session_maker() as session:
            org = Organization(name="History Chart Org", slug="history-chart-org")
            session.add(org)
            await session.flush()

            now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            session.add_all(
                [
                    BalanceHistory(
                        organization_id=org.id,
                        service="binance",
                        assets=[],
                        accounts=[],
                        total_usd=100.0,
                        actual=True,
                        created_at=now,
                    ),
                    BalanceHistory(
                        organization_id=org.id,
                        service="binance",
                        assets=[],
                        accounts=[],
                        total_usd=120.0,
                        actual=True,
                        created_at=now + timedelta(hours=2),
                    ),
                ]
            )
            await session.commit()

            response = await get_history_chart(
                service="binance",
                start_date=now,
                end_date=now + timedelta(hours=2),
                interval="hour",
                fill="forward",
                order="asc",
                db=session,
                organization_id=org.id,
                _=object(),
            )

            payload = response.model_dump()
            self.assertEqual(set(payload.keys()), {"service", "interval", "fill", "order", "points"})
            self.assertEqual(payload["interval"], "hour")
            self.assertEqual(payload["fill"], "forward")
            self.assertEqual(payload["order"], "asc")
            self.assertGreaterEqual(len(payload["points"]), 3)
            self.assertEqual(
                set(payload["points"][0].keys()),
                {"bucket_start", "bucket_end", "total_usd", "actual", "point_type"},
            )
