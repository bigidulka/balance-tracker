import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.core.config import get_settings
from app.services.metrics_service import metrics_service

T = TypeVar("T")


class HotResponseCache:
    def __init__(self, *, max_entries: int) -> None:
        self.max_entries = max(1, int(max_entries))
        self._values: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    @staticmethod
    def _copy(value: T) -> T:
        model_copy = getattr(value, "model_copy", None)
        if callable(model_copy):
            return model_copy(deep=True)
        return value

    async def get_or_set(
        self,
        key: str,
        *,
        ttl_seconds: float,
        loader: Callable[[], Awaitable[T]],
    ) -> T:
        ttl = max(0.0, float(ttl_seconds))
        if ttl <= 0:
            metrics_service.inc("api_hot_cache_bypass_total")
            return await loader()

        cached = await self._get(key, ttl_seconds=ttl)
        if cached is not None:
            metrics_service.inc("api_hot_cache_hit_total")
            return self._copy(cached)

        lock = await self._lock_for_key(key)
        async with lock:
            cached = await self._get(key, ttl_seconds=ttl)
            if cached is not None:
                metrics_service.inc("api_hot_cache_wait_hit_total")
                return self._copy(cached)

            metrics_service.inc("api_hot_cache_miss_total")
            value = await loader()
            await self._put(key, value)
            return self._copy(value)

    async def invalidate_prefix(self, prefix: str) -> int:
        async with self._guard:
            keys = [key for key in self._values if key.startswith(prefix)]
            for key in keys:
                self._values.pop(key, None)
            return len(keys)

    async def _get(self, key: str, *, ttl_seconds: float) -> Any | None:
        now = time.monotonic()
        async with self._guard:
            item = self._values.get(key)
            if item is None:
                return None
            cached_at, value = item
            if now - cached_at >= ttl_seconds:
                self._values.pop(key, None)
                return None
            self._values.move_to_end(key)
            return value

    async def _put(self, key: str, value: Any) -> None:
        async with self._guard:
            self._values[key] = (time.monotonic(), value)
            self._values.move_to_end(key)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)

    async def _lock_for_key(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock


settings = get_settings()
hot_response_cache = HotResponseCache(max_entries=settings.api_hot_cache_max_entries)
