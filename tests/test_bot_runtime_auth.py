import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot import api_client as api_client_module
from bot.services import runtime


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def json(self, content_type=None):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(str(self._payload))


class _FakeSession:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[dict[str, object]] = []

    def post(self, url, *, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        return _FakeResponse(self.payload, self.status)


class BotRuntimeAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        runtime._auth_sessions_fallback.clear()
        runtime._user_settings_fallback.clear()
        runtime._subscribers_fallback.clear()
        runtime.set_current_telegram_user_id(None)
        runtime.set_current_backend_auth_session(None)

    async def asyncTearDown(self) -> None:
        runtime._auth_sessions_fallback.clear()
        runtime._user_settings_fallback.clear()
        runtime._subscribers_fallback.clear()
        runtime.set_current_telegram_user_id(None)
        runtime.set_current_backend_auth_session(None)

    def _settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            bot_token="bot-token",
            api_url="http://api.local",
            api_token="service-token",
            api_org_id="77",
            redis_url="redis://unused",
            allowed_users=(),
            admin_users=(6238100241,),
            hide_small_balance_threshold=1.0,
            notification_interval=60,
            min_stablecoin_change=1.0,
            min_token_change_percent=0.1,
            no_backend_ui_mode=False,
        )

    async def test_admin_is_strictly_pinned(self) -> None:
        with patch.object(runtime, "settings", self._settings()):
            self.assertTrue(await runtime.is_local_user_admin(6238100241))
            self.assertFalse(await runtime.is_local_user_admin(1001))

    async def test_user_allowed_only_after_cached_backend_auth(self) -> None:
        with patch.object(runtime, "settings", self._settings()), patch.object(
            runtime, "get_backend_auth_session", new=AsyncMock(side_effect=[None, {"access_token": "x"}])
        ):
            self.assertFalse(await runtime.is_local_user_allowed(1001))
            self.assertTrue(await runtime.is_local_user_allowed(1001))

    async def test_ensure_backend_auth_session_caches_per_telegram_user(self) -> None:
        fake_bootstrap = AsyncMock(
            return_value={
                "access_token": "token-1001",
                "user_id": 501,
                "organization_id": 701,
                "organization_name": "Telegram 1001",
                "role": "owner",
                "is_platform_admin": False,
            }
        )
        with patch.object(runtime, "settings", self._settings()), patch.object(
            runtime, "get_runtime_redis", new=AsyncMock(return_value=None)
        ), patch.object(api_client_module.api_client, "bootstrap_telegram_identity", new=fake_bootstrap):
            first = await runtime.ensure_backend_auth_session(
                telegram_user_id=1001,
                username="alice",
                full_name="Alice",
            )
            second = await runtime.ensure_backend_auth_session(
                telegram_user_id=1001,
                username="alice",
                full_name="Alice",
            )

        self.assertEqual(fake_bootstrap.await_count, 1)
        self.assertEqual(first["organization_id"], 701)
        self.assertEqual(second["organization_id"], 701)
        self.assertEqual(runtime._auth_sessions_fallback[1001]["access_token"], "token-1001")
        self.assertEqual(
            runtime._auth_sessions_fallback[1001]["version"],
            runtime.BACKEND_AUTH_SESSION_VERSION,
        )

    async def test_ensure_backend_auth_session_rebootstraps_when_profile_changes(self) -> None:
        fake_bootstrap = AsyncMock(
            side_effect=[
                {
                    "access_token": "token-1001-a",
                    "user_id": 501,
                    "organization_id": 701,
                    "organization_name": "Telegram 1001",
                    "role": "owner",
                    "is_platform_admin": False,
                },
                {
                    "access_token": "token-1001-b",
                    "user_id": 501,
                    "organization_id": 701,
                    "organization_name": "Telegram 1001",
                    "role": "owner",
                    "is_platform_admin": False,
                },
            ]
        )
        with patch.object(runtime, "settings", self._settings()), patch.object(
            runtime, "get_runtime_redis", new=AsyncMock(return_value=None)
        ), patch.object(api_client_module.api_client, "bootstrap_telegram_identity", new=fake_bootstrap):
            await runtime.ensure_backend_auth_session(
                telegram_user_id=1001,
                username="alice",
                full_name="Alice",
            )
            refreshed = await runtime.ensure_backend_auth_session(
                telegram_user_id=1001,
                username="alice_new",
                full_name="Alice Updated",
            )

        self.assertEqual(fake_bootstrap.await_count, 2)
        self.assertEqual(refreshed["access_token"], "token-1001-b")
        self.assertEqual(refreshed["username"], "alice_new")
        self.assertEqual(refreshed["full_name"], "Alice Updated")

    async def test_bootstrap_telegram_identity_uses_service_auth_headers(self) -> None:
        fake_session = _FakeSession(
            {
                "access_token": "token-2002",
                "user_id": 6002,
                "organization_id": 8002,
                "organization_name": "Telegram 2002",
                "role": "owner",
                "is_platform_admin": False,
            }
        )

        with patch.object(api_client_module, "settings", self._settings()), patch.object(
            api_client_module.api_client, "_get_session", new=AsyncMock(return_value=fake_session)
        ):
            payload = await api_client_module.api_client.bootstrap_telegram_identity(
                telegram_user_id=2002,
                telegram_username="bob",
                telegram_full_name="Bob",
            )

        self.assertEqual(payload["organization_id"], 8002)
        self.assertEqual(fake_session.calls[0]["headers"]["Authorization"], "Bearer service-token")
        self.assertEqual(fake_session.calls[0]["headers"]["X-Organization-Id"], "77")
        self.assertEqual(fake_session.calls[0]["json"]["telegram_user_id"], 2002)

    async def test_get_backend_auth_session_ignores_legacy_unversioned_cache(self) -> None:
        runtime._auth_sessions_fallback[1001] = {
            "telegram_user_id": 1001,
            "access_token": "legacy-token",
            "organization_id": 701,
            "backend_user_id": 501,
        }
        with patch.object(runtime, "get_runtime_redis", new=AsyncMock(return_value=None)):
            payload = await runtime.get_backend_auth_session(1001)
        self.assertIsNone(payload)
