import asyncio
import unittest

from app.services.response_cache import HotResponseCache


class HotResponseCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_get_or_set_singleflights_loader(self):
        cache = HotResponseCache(max_entries=8)
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"value": calls}

        results = await asyncio.gather(
            *[
                cache.get_or_set("same-key", ttl_seconds=1.0, loader=loader)
                for _ in range(10)
            ]
        )

        self.assertEqual(calls, 1)
        self.assertEqual(results, [{"value": 1}] * 10)

    async def test_get_or_set_expires_values(self):
        cache = HotResponseCache(max_entries=8)
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            return calls

        first = await cache.get_or_set("key", ttl_seconds=0.001, loader=loader)
        await asyncio.sleep(0.01)
        second = await cache.get_or_set("key", ttl_seconds=0.001, loader=loader)

        self.assertEqual(first, 1)
        self.assertEqual(second, 2)


if __name__ == "__main__":
    unittest.main()
