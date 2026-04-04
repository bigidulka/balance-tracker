import unittest
from datetime import datetime, timedelta, timezone

from bot.services.screen_service import ScreenService


class _StubApiRepository:
    pass


class _StubUserRepository:
    pass


class ScreenRefreshLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ScreenService(_StubApiRepository(), _StubUserRepository())

    def test_refresh_time_label_formats_seconds(self):
        dt = datetime.now(timezone.utc) - timedelta(seconds=8)
        label = self.service._refresh_time_label(dt=dt, locale="en")
        self.assertEqual(label, "8s ago")

    def test_refresh_time_label_formats_minutes(self):
        dt = datetime.now(timezone.utc) - timedelta(minutes=3, seconds=5)
        label = self.service._refresh_time_label(dt=dt, locale="en")
        self.assertEqual(label, "3:05 ago")

    def test_refresh_time_label_formats_hours(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=2, minutes=4, seconds=9)
        label = self.service._refresh_time_label(dt=dt, locale="en")
        self.assertEqual(label, "2:04:09 ago")

    def test_latest_balance_update_uses_max_updated_at(self):
        now = datetime.now(timezone.utc)
        latest = self.service._latest_balance_update(
            {
                "services": [
                    {"service": "binance", "updated_at": (now - timedelta(minutes=5)).isoformat()},
                    {"service": "bybit", "updated_at": (now - timedelta(minutes=1)).isoformat()},
                ]
            }
        )
        self.assertEqual(latest, now - timedelta(minutes=1))

    def test_latest_exchange_update_filters_by_exchange(self):
        now = datetime.now(timezone.utc)
        latest = self.service._latest_exchange_update(
            {
                "services": [
                    {"service": "binance", "updated_at": (now - timedelta(minutes=5)).isoformat()},
                    {"service": "bybit", "updated_at": (now - timedelta(minutes=1)).isoformat()},
                ]
            },
            "binance",
        )
        self.assertEqual(latest, now - timedelta(minutes=5))

