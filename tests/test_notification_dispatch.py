import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot import notifications


class FakeBot:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.sent = []

    async def send_message(self, user_id, message, parse_mode=None):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append((user_id, message, parse_mode))


class NotificationDispatchTests(unittest.IsolatedAsyncioTestCase):
    def _token(self):
        return SimpleNamespace(var=SimpleNamespace(reset=lambda token: None))

    async def test_check_and_send_notification_events_marks_sent(self):
        bot = FakeBot()
        event = {"id": 7, "title": "okx needs attention", "body": "401 Unauthorized"}
        with (
            patch.object(notifications, "get_notification_recipients", AsyncMock(return_value=[123])),
            patch.object(notifications, "ensure_backend_auth_session", AsyncMock(return_value={})),
            patch.object(notifications, "set_current_telegram_user_id", return_value=self._token()),
            patch.object(notifications, "set_current_backend_auth_session", return_value=self._token()),
            patch.object(notifications.api_client, "generate_notifications", AsyncMock(return_value={"status": "ok"})),
            patch.object(notifications.api_client, "get_notification_events", AsyncMock(return_value={"events": [event]})),
            patch.object(notifications.api_client, "mark_notification_event_sent", AsyncMock(return_value={"status": "ok"})) as mark_sent,
            patch.object(notifications.api_client, "mark_notification_event_failed", AsyncMock(return_value={"status": "ok"})) as mark_failed,
        ):
            await notifications.check_and_send_notification_events(bot)

        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.sent[0][0], 123)
        self.assertIn("okx needs attention", bot.sent[0][1])
        mark_sent.assert_awaited_once_with(7)
        mark_failed.assert_not_awaited()

    async def test_check_and_send_notification_events_marks_failed_on_send_error(self):
        bot = FakeBot(fail=True)
        event = {"id": 8, "title": "tx", "body": "deposit"}
        with (
            patch.object(notifications, "get_notification_recipients", AsyncMock(return_value=[123])),
            patch.object(notifications, "ensure_backend_auth_session", AsyncMock(return_value={})),
            patch.object(notifications, "set_current_telegram_user_id", return_value=self._token()),
            patch.object(notifications, "set_current_backend_auth_session", return_value=self._token()),
            patch.object(notifications.api_client, "generate_notifications", AsyncMock(return_value={"status": "ok"})),
            patch.object(notifications.api_client, "get_notification_events", AsyncMock(return_value={"events": [event]})),
            patch.object(notifications.api_client, "mark_notification_event_sent", AsyncMock(return_value={"status": "ok"})) as mark_sent,
            patch.object(notifications.api_client, "mark_notification_event_failed", AsyncMock(return_value={"status": "ok"})) as mark_failed,
        ):
            await notifications.check_and_send_notification_events(bot)

        mark_sent.assert_not_awaited()
        mark_failed.assert_awaited_once()
        self.assertEqual(mark_failed.await_args.args[0], 8)
        self.assertIn("send failed", mark_failed.await_args.args[1])


if __name__ == "__main__":
    unittest.main()
