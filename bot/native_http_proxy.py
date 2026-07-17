from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Dict, Optional, cast
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientTimeout
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType

_PROXY_HOST = "***REMOVED***"
_PROXY_PORT = 49855
_SAFE_RETRY_METHODS = {"getMe", "getUpdates"}


def _validate_proxy_url(proxy: str) -> str:
    try:
        parsed = urlsplit(proxy)
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError("Invalid outbound proxy") from None

    if (
        parsed.scheme.lower() != "http"
        or parsed.hostname != _PROXY_HOST
        or port != _PROXY_PORT
        or not parsed.username
        or not parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid outbound proxy")
    return proxy


class NativeHttpProxySession(AiohttpSession):
    def __init__(self, proxy: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._native_proxy = _validate_proxy_url(proxy)

    async def make_request(
        self, bot: Any, method: TelegramMethod[TelegramType], timeout: Optional[int] = None
    ) -> TelegramType:
        session = await self.create_session()
        url = self.api.api_url(token=bot.token, method=method.__api_method__)
        effective_timeout = self.timeout if timeout is None else timeout
        attempts = 3 if method.__api_method__ in _SAFE_RETRY_METHODS else 1
        loop = asyncio.get_running_loop()
        deadline = loop.time() + effective_timeout if attempts > 1 else None

        for attempt in range(attempts):
            request_timeout = effective_timeout
            if deadline is not None:
                request_timeout = deadline - loop.time()
                if request_timeout <= 0:
                    raise TelegramNetworkError(
                        method=method, message="Request failed: TimeoutError"
                    ) from None

            form = self.build_form_data(bot=bot, method=method)
            try:
                async with session.post(
                    url,
                    data=form,
                    timeout=ClientTimeout(total=request_timeout),
                    proxy=self._native_proxy,
                ) as resp:
                    raw_result = await resp.text()
            except (asyncio.TimeoutError, ClientError) as exc:
                if attempt == attempts - 1:
                    raise TelegramNetworkError(
                        method=method,
                        message=f"Request failed: {type(exc).__name__}",
                    ) from None
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TelegramNetworkError(
                            method=method, message="Request failed: TimeoutError"
                        ) from None
                    await asyncio.sleep(min(0.2 * (attempt + 1), remaining))

            else:
                response = self.check_response(
                    bot=bot, method=method, status_code=resp.status, content=raw_result
                )
                return cast(TelegramType, response.result)

        raise AssertionError("unreachable")

    async def stream_content(
        self,
        url: str,
        headers: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        if headers is None:
            headers = {}

        session = await self.create_session()
        async with session.get(
            url,
            timeout=ClientTimeout(total=timeout),
            headers=headers,
            raise_for_status=raise_for_status,
            proxy=self._native_proxy,
        ) as resp:
            async for chunk in resp.content.iter_chunked(chunk_size):
                yield chunk
