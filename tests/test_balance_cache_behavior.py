import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.schemas.balance import ServiceBalanceSchema
from app.services.balance_service import BalanceService, _balance_cache


class BalanceCacheBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _balance_cache.clear()

    async def asyncTearDown(self):
        _balance_cache.clear()

    async def test_fresh_cache_returns_without_revalidation(self):
        session = object()
        service = "binance"
        now = datetime.now(timezone.utc)
        cached_balance = ServiceBalanceSchema(service=service, total_usd=100.0, updated_at=now)

        with (
            patch("app.services.balance_service.settings") as mock_settings,
            patch("app.services.balance_service.time.time") as mock_time,
            patch("app.services.balance_service.metrics_service.inc") as mock_metrics,
            patch("app.services.balance_service.ccxt_manager.fetch_balance", new_callable=AsyncMock) as fetch_balance,
            patch.object(BalanceService, "load_balance_targets", new=AsyncMock(return_value=([{"kind": "cex", "integration_id": 1, "service": service, "source_id": service}], {service}))),
        ):
            mock_settings.balance_cache_ttl = 60
            mock_settings.balance_cache_hard_ttl = 120
            mock_settings.enable_ccxt_stale_revalidate = True
            mock_settings.exchange_parallelism = 2

            _balance_cache[(1, service, None)] = {
                "balance": cached_balance,
                "cached_at": 100.0,
                "state": "fresh",
            }
            mock_time.return_value = 120.0

            svc = BalanceService(session=session, organization_id=1)
            result = await svc.get_all_balances(force_refresh=False)

            fetch_balance.assert_not_awaited()
            self.assertEqual(len(result.services), 1)
            self.assertIs(result.services[0], cached_balance)
            self.assertEqual(result.total_usd, 100.0)
            mock_metrics.assert_any_call("ccxt_cache_l1_hit_total")

    async def test_stale_cache_serves_and_revalidates_when_enabled(self):
        session = object()
        service = "binance"
        now = datetime.now(timezone.utc)
        stale_balance = ServiceBalanceSchema(service=service, total_usd=100.0, updated_at=now)
        refreshed_balance = ServiceBalanceSchema(service=service, total_usd=150.0, updated_at=now)

        with (
            patch("app.services.balance_service.settings") as mock_settings,
            patch("app.services.balance_service.time.time") as mock_time,
            patch("app.services.balance_service.metrics_service.inc") as mock_metrics,
            patch("app.services.balance_service.ccxt_manager.fetch_balance", new_callable=AsyncMock) as fetch_balance,
            patch.object(BalanceService, "fetch_and_save_balance", new_callable=AsyncMock) as fetch_and_save,
            patch.object(BalanceService, "handle_fetch_error", new_callable=AsyncMock) as handle_error,
            patch.object(BalanceService, "load_balance_targets", new=AsyncMock(return_value=([{"kind": "cex", "integration_id": 1, "service": service, "source_id": service}], {service}))),
        ):
            mock_settings.balance_cache_ttl = 60
            mock_settings.balance_cache_hard_ttl = 120
            mock_settings.enable_ccxt_stale_revalidate = True
            mock_settings.exchange_parallelism = 2

            _balance_cache[(1, service, None)] = {
                "balance": stale_balance,
                "cached_at": 100.0,
                "state": "fresh",
            }
            mock_time.return_value = 170.0

            fetch_balance.return_value = refreshed_balance
            fetch_and_save.return_value = refreshed_balance
            handle_error.return_value = None

            svc = BalanceService(session=session, organization_id=1)
            result = await svc.get_all_balances(force_refresh=False)

            fetch_balance.assert_awaited_once_with(service)
            fetch_and_save.assert_awaited_once_with(service, refreshed_balance)
            self.assertEqual(len(result.services), 1)
            self.assertIs(result.services[0], refreshed_balance)
            self.assertEqual(result.total_usd, 150.0)
            mock_metrics.assert_any_call("ccxt_cache_l1_stale_served_total")

    async def test_stale_cache_error_keeps_stale_value_when_revalidate_enabled(self):
        session = object()
        service = "binance"
        now = datetime.now(timezone.utc)
        stale_balance = ServiceBalanceSchema(service=service, total_usd=100.0, updated_at=now)
        fallback_balance = ServiceBalanceSchema(service=service, total_usd=10.0, updated_at=now)

        with (
            patch("app.services.balance_service.settings") as mock_settings,
            patch("app.services.balance_service.time.time") as mock_time,
            patch("app.services.balance_service.metrics_service.inc"),
            patch("app.services.balance_service.ccxt_manager.fetch_balance", new_callable=AsyncMock) as fetch_balance,
            patch.object(BalanceService, "fetch_and_save_balance", new_callable=AsyncMock) as fetch_and_save,
            patch.object(BalanceService, "handle_fetch_error", new_callable=AsyncMock) as handle_error,
            patch.object(BalanceService, "load_balance_targets", new=AsyncMock(return_value=([{"kind": "cex", "integration_id": 1, "service": service, "source_id": service}], {service}))),
        ):
            mock_settings.balance_cache_ttl = 60
            mock_settings.balance_cache_hard_ttl = 120
            mock_settings.enable_ccxt_stale_revalidate = True
            mock_settings.exchange_parallelism = 2

            _balance_cache[(1, service, None)] = {
                "balance": stale_balance,
                "cached_at": 100.0,
                "state": "fresh",
            }
            mock_time.return_value = 170.0

            fetch_balance.side_effect = RuntimeError("fetch failed")
            handle_error.return_value = fallback_balance

            svc = BalanceService(session=session, organization_id=1)
            result = await svc.get_all_balances(force_refresh=False)

            fetch_and_save.assert_not_awaited()
            handle_error.assert_awaited_once()
            self.assertEqual(len(result.services), 1)
            self.assertIs(result.services[0], stale_balance)
            self.assertEqual(result.total_usd, 100.0)

    async def test_hard_expired_cache_misses_and_drops_entry(self):
        session = object()
        service = "binance"
        now = datetime.now(timezone.utc)

        with (
            patch("app.services.balance_service.settings") as mock_settings,
            patch("app.services.balance_service.time.time") as mock_time,
            patch("app.services.balance_service.metrics_service.inc") as mock_metrics,
            patch("app.services.balance_service.ccxt_manager.fetch_balance", new_callable=AsyncMock) as fetch_balance,
            patch.object(BalanceService, "fetch_and_save_balance", new_callable=AsyncMock) as fetch_and_save,
            patch.object(BalanceService, "handle_fetch_error", new_callable=AsyncMock) as handle_error,
            patch.object(BalanceService, "load_balance_targets", new=AsyncMock(return_value=([{"kind": "cex", "integration_id": 1, "service": service, "source_id": service}], {service}))),
        ):
            mock_settings.balance_cache_ttl = 60
            mock_settings.balance_cache_hard_ttl = 120
            mock_settings.enable_ccxt_stale_revalidate = True
            mock_settings.exchange_parallelism = 2

            expired_balance = ServiceBalanceSchema(service=service, total_usd=55.0, updated_at=now)
            refreshed_balance = ServiceBalanceSchema(service=service, total_usd=77.0, updated_at=now)
            _balance_cache[(1, service, None)] = {
                "balance": expired_balance,
                "cached_at": 100.0,
                "state": "fresh",
            }
            mock_time.return_value = 260.0

            fetch_balance.return_value = refreshed_balance
            fetch_and_save.return_value = refreshed_balance
            handle_error.return_value = None

            svc = BalanceService(session=session, organization_id=1)
            result = await svc.get_all_balances(force_refresh=False)

            mock_metrics.assert_any_call("ccxt_cache_miss_total")
            fetch_balance.assert_awaited_once_with(service)
            fetch_and_save.assert_awaited_once_with(service, refreshed_balance)
            self.assertNotIn((1, service, None), _balance_cache)
            self.assertEqual(len(result.services), 1)
            self.assertIs(result.services[0], refreshed_balance)
            self.assertEqual(result.total_usd, 77.0)

    async def test_no_active_integrations_returns_empty_portfolio(self):
        session = object()

        with patch.object(
            BalanceService,
            "load_balance_targets",
            new=AsyncMock(return_value=([], set())),
        ):
            svc = BalanceService(session=session, organization_id=1)
            result = await svc.get_all_balances(force_refresh=False)

        self.assertEqual(result.total_usd, 0.0)
        self.assertEqual(result.services, [])

    async def test_degraded_balance_uses_error_path_and_fallback(self):
        session = object()
        service = "binance"
        now = datetime.now(timezone.utc)
        degraded_balance = ServiceBalanceSchema(
            service=service,
            total_usd=0.0,
            updated_at=now,
            actual=False,
        )
        fallback_balance = ServiceBalanceSchema(
            service=service,
            total_usd=100.0,
            updated_at=now,
            actual=False,
        )

        with (
            patch("app.services.balance_service.settings") as mock_settings,
            patch("app.services.balance_service.ccxt_manager.fetch_balance", new_callable=AsyncMock) as fetch_balance,
            patch.object(BalanceService, "handle_fetch_error", new_callable=AsyncMock) as handle_error,
            patch.object(BalanceService, "fetch_and_save_balance", new_callable=AsyncMock) as fetch_and_save,
            patch.object(BalanceService, "load_balance_targets", new=AsyncMock(return_value=([{"kind": "cex", "integration_id": 1, "service": service, "source_id": service}], {service}))),
        ):
            mock_settings.exchange_parallelism = 2
            fetch_balance.return_value = degraded_balance
            handle_error.return_value = fallback_balance

            svc = BalanceService(session=session, organization_id=1)
            result = await svc.get_all_balances(force_refresh=True)

        fetch_and_save.assert_not_awaited()
        handle_error.assert_awaited_once()
        self.assertEqual(result.total_usd, 100.0)
        self.assertIs(result.services[0], fallback_balance)

    async def test_fetch_and_save_balance_returns_persisted_balance_shape(self):
        session = object()
        service = "binance"
        now = datetime.now(timezone.utc)
        incoming = ServiceBalanceSchema(
            service=service,
            total_usd=0.0,
            updated_at=now,
            actual=True,
        )

        persisted = type(
            "PersistedBalance",
            (),
            {
                "assets": [{"coin": "USDT", "amount": 0.0, "value_usd": 0.0}],
                "accounts": [],
                "total_usd": 0.0,
                "updated_at": now,
                "actual": True,
            },
        )()

        with (
            patch.object(BalanceService, "_set_cached_balance") as set_cache,
            patch("app.services.balance_service.BalanceRepository.save_balance", new_callable=AsyncMock) as save_balance,
            patch("app.services.balance_service.ServiceStatusRepository.update_status", new_callable=AsyncMock) as update_status,
        ):
            save_balance.return_value = persisted
            svc = BalanceService(session=session, organization_id=1)
            result = await svc.fetch_and_save_balance(service, incoming)

        update_status.assert_awaited_once()
        set_cache.assert_called_once()
        self.assertEqual(result.total_usd, 0.0)
        self.assertEqual(result.assets[0].coin, "USDT")
