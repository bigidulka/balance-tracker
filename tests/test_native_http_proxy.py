from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientError, ClientTimeout
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe, SendMessage

from bot import native_http_proxy
from bot.native_http_proxy import NativeHttpProxySession


class _Response:
    status = 200

    async def text(self):
        return '{"ok": true, "result": {}}'


class _RequestContext:
    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _StreamContent:
    def __init__(self, chunks):
        self.chunks = chunks
        self.chunk_size = None

    async def iter_chunked(self, chunk_size):
        self.chunk_size = chunk_size
        for chunk in self.chunks:
            yield chunk


class _StreamResponse:
    def __init__(self, chunks):
        self.content = _StreamContent(chunks)


class _ClientSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.post_calls = []
        self.get_calls = []
        self.stream_response = _StreamResponse([b"first", b"second"])

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        return _RequestContext(self.outcomes.pop(0))

    def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        return _RequestContext(self.stream_response)


class _FakeLoop:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now


class NativeHttpProxySessionTests(IsolatedAsyncioTestCase):
    proxy = "http://user:secret@***REMOVED***:49855"

    async def _request(self, outcomes, method=None):
        session = NativeHttpProxySession(proxy=self.proxy, timeout=10)
        client = _ClientSession(outcomes)
        create_session = AsyncMock(return_value=client)
        check_response = Mock(return_value=SimpleNamespace(result="ok"))
        forms = [object() for _ in outcomes]
        build_form_data = Mock(side_effect=forms)
        session.create_session = create_session
        session.check_response = check_response
        session.build_form_data = build_form_data
        bot = SimpleNamespace(token="bot-token", default={})
        return session, client, forms, build_form_data, bot, method or GetMe()

    async def test_safe_get_me_retries_with_deadline_and_native_proxy(self):
        session, client, forms, build_form_data, bot, method = await self._request(
            [ClientError(), ClientError(), _Response()]
        )
        loop = _FakeLoop()

        async def sleep(seconds):
            loop.now += seconds

        with (
            patch.object(native_http_proxy.asyncio, "get_running_loop", return_value=loop),
            patch.object(native_http_proxy.asyncio, "sleep", new=AsyncMock(side_effect=sleep)),
        ):
            result = await session.make_request(bot, method)

        self.assertEqual(result, "ok")
        self.assertIsNone(session.proxy)
        self.assertEqual(build_form_data.call_count, 3)
        self.assertEqual([call[1]["data"] for call in client.post_calls], forms)
        self.assertTrue(all(call[1]["proxy"] == self.proxy for call in client.post_calls))
        timeouts = [call[1]["timeout"] for call in client.post_calls]
        self.assertTrue(all(isinstance(timeout, ClientTimeout) for timeout in timeouts))
        self.assertEqual([timeout.total for timeout in timeouts], [10, 9.8, 9.4])

    async def test_unsafe_method_does_not_retry_network_error(self):
        method = SendMessage(chat_id=1, text="test")
        session, client, _, build_form_data, bot, method = await self._request(
            [ClientError(self.proxy)], method=method
        )

        with self.assertRaises(TelegramNetworkError) as raised:
            await session.make_request(bot, method)

        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(build_form_data.call_count, 1)
        self.assertIsNone(raised.exception.__cause__)
        self.assertNotIn("secret", str(raised.exception))

    async def test_safe_retries_exhausted_without_proxy_credentials(self):
        session, client, _, _, bot, method = await self._request(
            [ClientError(self.proxy), ClientError(self.proxy), ClientError(self.proxy)]
        )
        loop = _FakeLoop()

        async def sleep(seconds):
            loop.now += seconds

        with (
            patch.object(native_http_proxy.asyncio, "get_running_loop", return_value=loop),
            patch.object(native_http_proxy.asyncio, "sleep", new=AsyncMock(side_effect=sleep)),
            self.assertRaises(TelegramNetworkError) as raised,
        ):
            await session.make_request(bot, method)

        self.assertEqual(len(client.post_calls), 3)
        self.assertIn("ClientError", str(raised.exception))
        self.assertNotIn(self.proxy, str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    async def test_stream_content_uses_native_proxy(self):
        session, client, _, _, _, _ = await self._request([])

        chunks = [
            chunk
            async for chunk in session.stream_content(
                "https://example.test/file",
                headers={"X-Test": "value"},
                timeout=7,
                chunk_size=4,
                raise_for_status=False,
            )
        ]

        self.assertEqual(chunks, [b"first", b"second"])
        _, kwargs = client.get_calls[0]
        self.assertEqual(kwargs["proxy"], self.proxy)
        self.assertEqual(kwargs["headers"], {"X-Test": "value"})
        self.assertEqual(kwargs["timeout"].total, 7)
        self.assertFalse(kwargs["raise_for_status"])
        self.assertEqual(client.stream_response.content.chunk_size, 4)

    def test_invalid_proxy_urls_do_not_disclose_value(self):
        invalid_proxies = [
            "socks5://user:secret@***REMOVED***:49855",
            "https://user:secret@***REMOVED***:49855",
            "http://user:secret@other.example:49855",
            "http://user:secret@***REMOVED***:443",
            "http://***REMOVED***:49855",
            "http://user@***REMOVED***:49855",
            "http://user:secret@***REMOVED***:49855/path",
            "http://user:secret@***REMOVED***:49855?query=1",
            "http://user:secret@***REMOVED***:49855#fragment",
        ]

        for proxy in invalid_proxies:
            with self.subTest(proxy=proxy), self.assertRaises(ValueError) as raised:
                NativeHttpProxySession(proxy=proxy)
            self.assertNotIn(proxy, str(raised.exception))
            self.assertNotIn("secret", str(raised.exception))
