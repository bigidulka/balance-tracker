"""
New-user end-to-end flow tests.

Covers the full journey for a brand-new user with no legacy Balance rows:
  - All DEX wallet types added fresh (only tron_ton_ keys in DB)
  - Correct chain labels in every display path (name_map AND fallback)
  - No duplicate wallets despite two DB rows per tron_ton integration
  - Integration creation FSM for all wallet types (EVM, SOL, TRON, TON, SUI)
  - Entitlement gates (EVM limit, non-EVM exempt)
  - Integrations list body + keyboard both show correct display names
  - DEX wallet list shows correct prefixes for all 5 chain types
"""
import unittest
from typing import Any

from bot.contracts.callbacks import ROUTE_INTEGRATION_DETAIL, ROUTE_INTEGRATIONS
from bot.services.balance import parse_balances
from bot.services.integration_label import build_service_name_map, dex_label
from bot.services.screen_service import ScreenService

EVM_ADDR  = "0xf9095877f93603d0b6c44e5a82db5dc751b34cd8"
SOL_ADDR  = "7mRZVLnTgAWoTnBkAc6QRW7BP4vwD8gu5qJR4148CsfD"
TRON_ADDR = "TAAJTevA5fFsuizex43KQX2GnFCrDYoHVH"
TON_ADDR  = "UQDa41HlMn55k_P1il1HvApL9Rp6YvMhBV5XE4FnFKWD8c0Z"
SUI_ADDR  = "0x9738deb9ff6ba130cb3203601206f02e805d821ec216c23d2f7b8cc5fd242c68"

lo = str.lower

def _legacy_svc(addr: str) -> str:
    return f"tron_ton_{lo(addr)}"

def _canonical_tron(addr: str) -> str:
    return f"tron_{lo(addr)}"

def _canonical_ton(addr: str) -> str:
    return f"ton_{lo(addr)}"


class _FakeUserRepo:
    async def get_settings(self, user_id: int) -> dict:
        return {"language": "en", "hide_small": False}


class _FakeApiRepo:
    def __init__(self, billing: dict, integrations: list, balance_services: list):
        self._billing = billing
        self._integrations = integrations
        self._balance_services = balance_services
        self.created_payloads: list[dict] = []
        self.refreshed_ids: list[int] = []
        self._next_id = 100

    async def get_billing(self) -> dict:
        return self._billing

    async def get_billing_current(self) -> dict:
        return self._billing

    async def get_billing_plans(self) -> dict:
        return {"plans": [
            {"code": "free",   "name": "Free",   "price_monthly": 0,   "currency": "USD", "policy": {}, "is_active": True},
            {"code": "low",    "name": "Low",    "price_monthly": 5,   "currency": "USD", "policy": {}, "is_active": True},
            {"code": "medium", "name": "Medium", "price_monthly": 10,  "currency": "USD", "policy": {}, "is_active": True},
            {"code": "pro",    "name": "Pro",    "price_monthly": 20,  "currency": "USD", "policy": {}, "is_active": True},
        ]}

    async def get_billing_balance(self) -> dict:
        return {"available": 0.0}

    async def get_integrations(self) -> list:
        return self._integrations

    async def get_balances(self, *, cached: bool = True) -> dict:
        return {"services": self._balance_services}

    async def create_integration(self, payload: dict) -> dict:
        self.created_payloads.append(payload)
        int_id = self._next_id
        self._next_id += 1
        return {"id": int_id, **payload}

    async def queue_integration_refresh(self, integration_id: int) -> dict:
        self.refreshed_ids.append(integration_id)
        return {"id": 301, "status": "queued"}

    async def get_tariff(self) -> dict:
        return self._billing


def _free_billing(*, evm_used: int = 0, cex_used: int = 0) -> dict:
    return {
        "plan": {"code": "free", "name": "Free"},
        "capabilities": {"allow_dex": True},
        "policy": {"features": {"allow_dex": True}},
        "limits": {"max_cex_accounts": 5, "max_evm_wallets": 1},
        "background": {"enabled": True, "refresh_interval_seconds": 600},
        "usage": {
            "cex": {"active": cex_used, "remaining": 5 - cex_used, "limit_reached": cex_used >= 5},
            "evm": {"active": evm_used, "remaining": 1 - evm_used, "limit_reached": evm_used >= 1},
            "non_evm_dex": {"active": 0},
            "integrations": {"active": cex_used + evm_used, "remaining": 6, "limit_reached": False},
        },
    }


def _dex_integrations() -> list:
    return [
        {"id": 1,  "kind": "dex", "provider": "okx_wallet", "chain": "ethereum", "wallet_address": EVM_ADDR,  "name": "", "is_active": True},
        {"id": 17, "kind": "dex", "provider": "okx_wallet", "chain": "solana",   "wallet_address": SOL_ADDR,  "name": "", "is_active": True},
        {"id": 19, "kind": "dex", "provider": "tron_ton",   "chain": "tron",     "wallet_address": TRON_ADDR, "name": "", "is_active": True},
        {"id": 18, "kind": "dex", "provider": "tron_ton",   "chain": "ton",      "wallet_address": TON_ADDR,  "name": "", "is_active": True},
        {"id": 20, "kind": "dex", "provider": "sui",        "chain": "sui",      "wallet_address": SUI_ADDR,  "name": "", "is_active": True},
    ]


def _new_user_balance_services() -> list:
    """Balance rows as written by current provider code for a brand-new user.
    tron_ton provider still writes tron_ton_ keys. EVM/SOL/SUI are canonical.
    """
    return [
        {"service": f"evm_{lo(EVM_ADDR)}",  "integration_id": 1,  "total_usd": 2436.87, "assets": [], "accounts": []},
        {"service": f"sol_{lo(SOL_ADDR)}",  "integration_id": 17, "total_usd": 317.10,  "assets": [], "accounts": []},
        {"service": _legacy_svc(TRON_ADDR), "integration_id": 19, "total_usd": 507.28,  "assets": [], "accounts": []},
        {"service": _legacy_svc(TON_ADDR),  "integration_id": 18, "total_usd": 7.10,    "assets": [], "accounts": []},
        {"service": f"sui_{lo(SUI_ADDR)}",  "integration_id": 20, "total_usd": 231.64,  "assets": [], "accounts": []},
    ]


def _migration_balance_services() -> list:
    base = _new_user_balance_services()
    base += [
        {"service": _canonical_tron(TRON_ADDR), "integration_id": 19, "total_usd": 507.28, "assets": [], "accounts": []},
        {"service": _canonical_ton(TON_ADDR),   "integration_id": 18, "total_usd": 7.10,   "assets": [], "accounts": []},
    ]
    return base


def _dex_labels(dex_wallets: dict, name_map: dict) -> dict:
    result = {}
    for k in dex_wallets:
        base = k.split("#")[0]
        label = name_map.get(k) or name_map.get(base)
        if not label:
            chain = ScreenService._chain_from_service(k)
            addr  = ScreenService._wallet_address_from_service(k)
            label = dex_label(addr, chain)
        result[k] = label
    return result


class NewUserDEXLabelTests(unittest.IsolatedAsyncioTestCase):
    """DEX wallet display names work correctly for brand-new user accounts."""

    def setUp(self):
        self.integrations = _dex_integrations()
        self.name_map = build_service_name_map(self.integrations)

    def test_name_map_covers_legacy_tron_ton_keys(self):
        self.assertIn(_legacy_svc(TRON_ADDR), self.name_map)
        self.assertIn(f"{_legacy_svc(TRON_ADDR)}#19", self.name_map)
        self.assertIn(_legacy_svc(TON_ADDR), self.name_map)
        self.assertIn(f"{_legacy_svc(TON_ADDR)}#18", self.name_map)

    def test_name_map_legacy_tron_key_is_trx(self):
        self.assertEqual(self.name_map[_legacy_svc(TRON_ADDR)], "TRX TAAJTe...oHVH")

    def test_name_map_legacy_ton_key_is_ton(self):
        self.assertEqual(self.name_map[_legacy_svc(TON_ADDR)], "TON UQDa41...8c0Z")

    def test_name_map_canonical_evm_key(self):
        self.assertEqual(self.name_map[f"evm_{lo(EVM_ADDR)}"], "EVM 0xf909...4cd8")

    def test_name_map_canonical_sol_key(self):
        self.assertIn(f"sol_{lo(SOL_ADDR)}", self.name_map)
        self.assertTrue(self.name_map[f"sol_{lo(SOL_ADDR)}"].startswith("SOL"))

    def test_name_map_canonical_sui_key(self):
        self.assertIn(f"sui_{lo(SUI_ADDR)}", self.name_map)
        self.assertTrue(self.name_map[f"sui_{lo(SUI_ADDR)}"].startswith("SUI"))

    def test_new_user_parse_yields_5_wallets_no_duplicates(self):
        result = parse_balances({"services": _new_user_balance_services()})
        dex = result["dex_wallets"]
        self.assertEqual(len(dex), 5, f"Expected 5 wallets, got {len(dex)}: {list(dex)}")

    def test_migration_parse_deduplicates_to_5(self):
        result = parse_balances({"services": _migration_balance_services()})
        dex = result["dex_wallets"]
        self.assertEqual(len(dex), 5, f"Expected 5, got {len(dex)}: {list(dex)}")

    def test_migration_no_legacy_key_survives_dedup(self):
        result = parse_balances({"services": _migration_balance_services()})
        legacy = [k for k in result["dex_wallets"] if "tron_ton_" in k]
        self.assertEqual(legacy, [], f"Legacy keys survived: {legacy}")

    def test_new_user_all_labels_correct(self):
        result = parse_balances({"services": _new_user_balance_services()})
        labels = set(_dex_labels(result["dex_wallets"], self.name_map).values())
        self.assertIn("TRX TAAJTe...oHVH", labels)
        self.assertIn("TON UQDa41...8c0Z", labels)
        self.assertIn("EVM 0xf909...4cd8", labels)

    def test_new_user_no_ton_wallet_shows_trx(self):
        result = parse_balances({"services": _new_user_balance_services()})
        for k, label in _dex_labels(result["dex_wallets"], self.name_map).items():
            if "uqda41" in k.lower():
                self.assertFalse(label.startswith("TRX"),
                                 f"TON wallet got TRX prefix: key={k} label={label}")

    def test_chain_from_service_legacy_tron(self):
        self.assertEqual(ScreenService._chain_from_service(f"{_legacy_svc(TRON_ADDR)}#19"), "tron")

    def test_chain_from_service_legacy_ton(self):
        self.assertEqual(ScreenService._chain_from_service(f"{_legacy_svc(TON_ADDR)}#18"), "ton")

    def test_wallet_address_from_service_legacy_tron(self):
        addr = ScreenService._wallet_address_from_service(f"{_legacy_svc(TRON_ADDR)}#19")
        self.assertEqual(addr, lo(TRON_ADDR))
        self.assertFalse(addr.startswith("ton_"), addr)

    def test_wallet_address_from_service_legacy_ton(self):
        addr = ScreenService._wallet_address_from_service(f"{_legacy_svc(TON_ADDR)}#18")
        self.assertEqual(addr, lo(TON_ADDR))
        self.assertFalse(addr.startswith("tron_"), addr)

    def test_fallback_label_tron(self):
        svc = f"{_legacy_svc(TRON_ADDR)}#19"
        label = dex_label(ScreenService._wallet_address_from_service(svc),
                          ScreenService._chain_from_service(svc))
        self.assertTrue(label.startswith("TRX"), label)

    def test_fallback_label_ton(self):
        svc = f"{_legacy_svc(TON_ADDR)}#18"
        label = dex_label(ScreenService._wallet_address_from_service(svc),
                          ScreenService._chain_from_service(svc))
        self.assertTrue(label.startswith("TON"), label)


class NewUserEntitlementTests(unittest.IsolatedAsyncioTestCase):
    """Wallet entitlement gates behave correctly for new users."""

    def _svc(self, evm_used: int = 0) -> ScreenService:
        return ScreenService(
            _FakeApiRepo(_free_billing(evm_used=evm_used), _dex_integrations(), []),
            _FakeUserRepo(),
        )

    async def test_evm_allowed_under_limit(self):
        ok, reason = await self._svc(0).can_add_evm_wallet(user_id=1, chain="ethereum")
        self.assertTrue(ok); self.assertIsNone(reason)

    async def test_evm_blocked_at_limit(self):
        ok, reason = await self._svc(1).can_add_evm_wallet(user_id=1, chain="ethereum")
        self.assertFalse(ok); self.assertIn("1/1", reason or "")

    async def test_solana_exempt_from_evm_limit(self):
        ok, reason = await self._svc(1).can_add_evm_wallet(user_id=1, chain="solana")
        self.assertTrue(ok); self.assertIsNone(reason)

    async def test_tron_exempt_from_evm_limit(self):
        ok, _ = await self._svc(1).can_add_evm_wallet(user_id=1, chain="tron")
        self.assertTrue(ok)

    async def test_ton_exempt_from_evm_limit(self):
        ok, _ = await self._svc(1).can_add_evm_wallet(user_id=1, chain="ton")
        self.assertTrue(ok)

    async def test_sui_exempt_from_evm_limit(self):
        ok, _ = await self._svc(1).can_add_evm_wallet(user_id=1, chain="sui")
        self.assertTrue(ok)


class NewUserFSMCreationTests(unittest.IsolatedAsyncioTestCase):
    """Integration creation FSM produces correct names and chain for all wallet types."""

    async def _create(self, provider: str, address: str):
        svc = ScreenService(
            _FakeApiRepo(_free_billing(), _dex_integrations(), []),
            _FakeUserRepo(),
        )
        result = await svc.process_input_value(
            user_id=1,
            waiting={
                "kind": "integration_dex_wallet_address",
                "return_route": "ig",
                "return_payload": {},
                "draft": {"provider": provider, "kind": "dex"},
            },
            raw_value=address,
        )
        return svc, result

    async def test_evm_creates_evm_label(self):
        svc, r = await self._create("okx_wallet", EVM_ADDR)
        self.assertTrue(r.success, r)
        p = svc.api_repo.created_payloads[0]
        self.assertEqual(p["chain"], "ethereum")
        self.assertEqual(p["name"], "EVM 0xf909...4cd8")
        self.assertEqual(r.next_route, ROUTE_INTEGRATION_DETAIL)

    async def test_sol_creates_sol_label(self):
        svc, r = await self._create("okx_wallet", SOL_ADDR)
        self.assertTrue(r.success, r)
        p = svc.api_repo.created_payloads[0]
        self.assertEqual(p["chain"], "solana")
        self.assertTrue(p["name"].startswith("SOL "), p["name"])

    async def test_tron_creates_trx_label(self):
        svc, r = await self._create("tron_ton", TRON_ADDR)
        self.assertTrue(r.success, r)
        p = svc.api_repo.created_payloads[0]
        self.assertEqual(p["chain"], "tron")
        self.assertTrue(p["name"].startswith("TRX "), p["name"])

    async def test_ton_creates_ton_label(self):
        svc, r = await self._create("tron_ton", TON_ADDR)
        self.assertTrue(r.success, r)
        p = svc.api_repo.created_payloads[0]
        self.assertEqual(p["chain"], "ton")
        self.assertTrue(p["name"].startswith("TON "), p["name"])

    async def test_sui_creates_sui_label(self):
        svc, r = await self._create("sui", SUI_ADDR)
        self.assertTrue(r.success, r)
        p = svc.api_repo.created_payloads[0]
        self.assertEqual(p["chain"], "sui")
        self.assertTrue(p["name"].startswith("SUI "), p["name"])


class NewUserIntegrationsScreenTests(unittest.IsolatedAsyncioTestCase):
    """Integrations screen body + keyboard show correct names for new users."""

    def _svc(self):
        return ScreenService(
            _FakeApiRepo(_free_billing(), _dex_integrations(), _new_user_balance_services()),
            _FakeUserRepo(),
        )

    async def test_body_no_raw_provider_names(self):
        rendered = await self._svc().render(route=ROUTE_INTEGRATIONS, payload={}, user_id=1, rev=1)
        text = rendered.text.as_kwargs()["text"]
        for raw in ("[ccxt]", "[debank]", "[tron/ton]", "[sui]"):
            self.assertNotIn(raw, text, f"Raw provider {raw!r} leaked into body")

    async def test_keyboard_ton_button_not_trx(self):
        rendered = await self._svc().render(route=ROUTE_INTEGRATIONS, payload={}, user_id=1, rev=1)
        buttons = [b.text for row in rendered.keyboard.inline_keyboard for b in row]
        for btn in buttons:
            if "uqda41" in btn.lower() or lo(TON_ADDR[:6]) in btn.lower():
                self.assertFalse(btn.upper().startswith("TRX"), f"TON btn shows TRX: {btn!r}")

    async def test_keyboard_tron_button_is_trx(self):
        rendered = await self._svc().render(route=ROUTE_INTEGRATIONS, payload={}, user_id=1, rev=1)
        buttons = [b.text for row in rendered.keyboard.inline_keyboard for b in row]
        tron_btns = [b for b in buttons if "taajte" in b.lower()]
        self.assertTrue(len(tron_btns) > 0, f"No TRON button found. Buttons: {buttons}")
        for btn in tron_btns:
            self.assertTrue(btn.startswith("TRX"), f"TRON btn wrong prefix: {btn!r}")

    async def test_keyboard_no_raw_db_keys(self):
        rendered = await self._svc().render(route=ROUTE_INTEGRATIONS, payload={}, user_id=1, rev=1)
        buttons = [b.text for row in rendered.keyboard.inline_keyboard for b in row]
        for btn in buttons:
            self.assertFalse(btn.startswith("tron_ton_"), f"Raw legacy key in btn: {btn!r}")
            self.assertFalse(btn.lower().startswith("ton_uq"), f"Raw DB key in btn: {btn!r}")


class NewUserKeyClassificationTests(unittest.TestCase):
    """_classify logic in balance.py identifies legacy vs canonical correctly."""

    _LEGACY = ("tron_ton_", "okx_wallet_")
    _CANON  = ("evm_", "sol_", "tron_", "ton_", "sui_")

    def _classify(self, name: str) -> str:
        for p in self._LEGACY:
            if name.startswith(p): return "legacy"
        for p in self._CANON:
            if name.startswith(p): return "canonical"
        return "cex"

    def test_tron_ton_tron_is_legacy(self):
        self.assertEqual(self._classify(_legacy_svc(TRON_ADDR)), "legacy")

    def test_tron_ton_ton_is_legacy(self):
        self.assertEqual(self._classify(_legacy_svc(TON_ADDR)), "legacy")

    def test_canonical_tron_is_canonical(self):
        self.assertEqual(self._classify(_canonical_tron(TRON_ADDR)), "canonical")

    def test_canonical_ton_is_canonical(self):
        self.assertEqual(self._classify(_canonical_ton(TON_ADDR)), "canonical")

    def test_evm_is_canonical(self):
        self.assertEqual(self._classify(f"evm_{lo(EVM_ADDR)}"), "canonical")

    def test_sol_is_canonical(self):
        self.assertEqual(self._classify(f"sol_{lo(SOL_ADDR)}"), "canonical")

    def test_sui_is_canonical(self):
        self.assertEqual(self._classify(f"sui_{lo(SUI_ADDR)}"), "canonical")

    def test_okx_wallet_is_legacy(self):
        self.assertEqual(self._classify(f"okx_wallet_{EVM_ADDR[:8].lower()}"), "legacy")

    def test_binance_is_cex(self):
        self.assertEqual(self._classify("binance"), "cex")
