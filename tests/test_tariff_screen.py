import unittest
from datetime import datetime, timedelta, timezone

from bot.contracts.callbacks import ROUTE_PLAN
from bot.services.screen_service import ScreenService


class FakeApiRepo:
    def __init__(self, billing: dict, integrations: list[dict]):
        self.billing = billing
        self.integrations = integrations
        self.plans = {
            "plans": [
                {
                    "code": "free",
                    "name": "Free",
                    "price_monthly": 0,
                    "policy": {
                        "limits": {"max_cex_accounts": 5, "max_evm_wallets": 1},
                        "background": {"refresh_interval_seconds": 600},
                    },
                },
                {
                    "code": "low",
                    "name": "Low",
                    "price_monthly": 9,
                    "policy": {
                        "limits": {"max_cex_accounts": 14, "max_evm_wallets": 3},
                        "background": {"refresh_interval_seconds": 300},
                    },
                },
            ]
        }

    async def get_billing_current(self) -> dict:
        return self.billing

    async def get_billing_plans(self) -> dict:
        return self.plans

    async def get_capabilities(self) -> dict:
        return {}

    async def get_integrations(self) -> list[dict]:
        return self.integrations


class FakeUserRepo:
    async def get_settings(self, user_id: int) -> dict:
        return {"language": "en"}

    async def is_local_allowed(self, user_id: int) -> bool:
        return True


class TariffScreenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        now = datetime.now(timezone.utc)
        self.billing = {
            "plan": {"code": "free", "name": "Free"},
            "last_refresh_at": (now - timedelta(minutes=2)).isoformat(),
            "capabilities": {"allow_dex": True},
            "policy": {"features": {"allow_dex": True}},
            "limits": {"max_cex_accounts": 5, "max_evm_wallets": 1},
            "background": {"enabled": True, "refresh_interval_seconds": 600},
            "usage": {
                "cex": {"active": 2, "remaining": 3, "limit_reached": False},
                "evm": {"active": 1, "remaining": 0, "limit_reached": True},
                "non_evm_dex": {"active": 1},
                "integrations": {"active": 3, "remaining": 3, "limit_reached": False},
            },
        }
        self.integrations = [
            {"kind": "cex", "is_active": True},
            {"kind": "cex", "is_active": True},
            {"kind": "dex", "chain": "ethereum", "is_active": True},
            {"kind": "dex", "chain": "solana", "is_active": True},
        ]
        self.service = ScreenService(FakeApiRepo(self.billing, self.integrations), FakeUserRepo())

    async def test_plan_screen_renders_usage_and_refresh_interval(self):
        rendered = await self.service.render(route=ROUTE_PLAN, payload={}, user_id=1, rev=1)
        text = rendered.text.as_kwargs()["text"]
        self.assertIn("Current plan", text)
        self.assertIn("CEX accounts", text)
        self.assertIn("2/5", text)
        self.assertIn("1/1", text)
        self.assertIn("10m", text)
        self.assertIn("Available plans", text)
        self.assertIn("Low (LOW)", text)

    async def test_legacy_full_plan_is_normalized(self):
        legacy_billing = dict(self.billing)
        legacy_billing["plan"] = {"code": "full", "name": "Full"}
        legacy_service = ScreenService(FakeApiRepo(legacy_billing, self.integrations), FakeUserRepo())
        rendered = await legacy_service.render(route=ROUTE_PLAN, payload={}, user_id=1, rev=1)
        text = rendered.text.as_kwargs()["text"]
        self.assertIn("Current plan: Pro (PRO)", text)

    async def test_cex_limit_blocks_add_exchange(self):
        full_billing = dict(self.billing)
        full_billing["usage"] = {
            "cex": {"active": 5, "remaining": 0, "limit_reached": True},
            "evm": {"active": 1, "remaining": 0, "limit_reached": True},
            "non_evm_dex": {"active": 1},
            "integrations": {"active": 6, "remaining": 0, "limit_reached": True},
        }
        full_service = ScreenService(FakeApiRepo(full_billing, self.integrations), FakeUserRepo())
        blocked, reason = await full_service.can_add_cex_account(user_id=1)
        self.assertFalse(blocked)
        self.assertIn("5/5", reason or "")

    async def test_evm_limit_blocks_only_evm_chains(self):
        blocked_evm, reason_evm = await self.service.can_add_evm_wallet(user_id=1, chain="ethereum")
        self.assertFalse(blocked_evm)
        self.assertIn("1/1", reason_evm or "")

        allowed_non_evm, reason_non_evm = await self.service.can_add_evm_wallet(user_id=1, chain="solana")
        self.assertTrue(allowed_non_evm)
        self.assertIsNone(reason_non_evm)
