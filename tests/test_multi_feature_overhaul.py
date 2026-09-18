"""Comprehensive tests for multi-feature overhaul:

1. Gate.io unified account deduplication
2. Display currency in settings
3. Tariff payment-only restriction
4. Notifications stub
5. Dashboard trader metrics
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone


# ============================================================
# 1. Gate.io unified account deduplication
# ============================================================

class TestMergeAccountsDeduplication(unittest.TestCase):
    """Test _merge_accounts with mirror_of deduplication."""

    def _call_merge(self, service, accounts_payload, calc_usd=None):
        """Call _merge_accounts via direct import from exchange_rest."""
        from app.services.exchange_rest import _merge_accounts
        if calc_usd is None:
            def calc_usd(coin, amount, tickers):
                # Stablecoins = 1:1 with USD
                if coin.upper() in ("USDT", "USDC", "USD", "DAI"):
                    return amount
                return 0.0
        return _merge_accounts(service, accounts_payload, calc_usd)

    def test_unified_gateio_dedup_total(self):
        """In unified mode, total_usd should not double-count mirrored futures."""
        result = self._call_merge("gateio", [
            {
                "account_type": "spot",
                "tickers": {},
                "assets": [{"coin": "USDT", "amount": 287.6}],
            },
            {
                "account_type": "futures",
                "tickers": {},
                "assets": [{"coin": "USDT", "amount": 287.6}],
                "mirror_of": "spot",
            },
        ])
        # Total should be 287.6 (deduped), not 575.2
        self.assertAlmostEqual(result.total_usd, 287.6, places=1)
        # Both accounts still visible for display
        self.assertEqual(len(result.accounts), 2)
        self.assertEqual(result.accounts[0].account_type, "spot")
        self.assertEqual(result.accounts[1].account_type, "futures")
        # Each account shows full balance for display
        self.assertAlmostEqual(result.accounts[0].total_usd, 287.6, places=1)
        self.assertAlmostEqual(result.accounts[1].total_usd, 287.6, places=1)

    def test_non_unified_counts_both(self):
        """Without mirror_of, both spot and futures count towards total."""
        result = self._call_merge("binance", [
            {
                "account_type": "spot",
                "tickers": {},
                "assets": [{"coin": "USDT", "amount": 200}],
            },
            {
                "account_type": "futures",
                "tickers": {},
                "assets": [{"coin": "USDT", "amount": 87.6}],
            },
        ])
        self.assertAlmostEqual(result.total_usd, 287.6, places=1)

    def test_mirror_assets_not_in_flat(self):
        """Mirrored account assets should not appear in flat_assets."""
        result = self._call_merge("gateio", [
            {
                "account_type": "spot",
                "tickers": {},
                "assets": [
                    {"coin": "USDT", "amount": 100},
                    {"coin": "BTC", "amount": 0.5},
                ],
            },
            {
                "account_type": "futures",
                "tickers": {},
                "assets": [
                    {"coin": "USDT", "amount": 100},
                    {"coin": "BTC", "amount": 0.5},
                ],
                "mirror_of": "spot",
            },
        ])
        # Flat assets should have unique coins, not doubled
        asset_map = {a.coin: a for a in result.assets}
        self.assertIn("USDT", asset_map)
        self.assertIn("BTC", asset_map)
        self.assertAlmostEqual(asset_map["USDT"].amount, 100.0, places=1)
        # BTC value_usd is 0 because no tickers and not stablecoin
        self.assertAlmostEqual(asset_map["BTC"].value_usd, 0.0, places=1)

    def test_zero_balance_account_kept_without_affecting_totals(self):
        """A zero-balance account stays visible for display but contributes nothing."""
        result = self._call_merge("gateio", [
            {
                "account_type": "spot",
                "tickers": {},
                "assets": [{"coin": "USDT", "amount": 0}],
            },
        ])
        self.assertEqual(len(result.accounts), 1)
        self.assertEqual(result.accounts[0].account_type, "spot")
        self.assertEqual(result.accounts[0].assets, [])
        self.assertEqual(result.assets, [])
        self.assertAlmostEqual(result.total_usd, 0.0)


class TestGateIoUnifiedDetection(unittest.IsolatedAsyncioTestCase):
    """Test the unified account detection heuristic."""

    async def test_detect_unified_with_matching_usdt(self):
        from app.services.exchange_rest import GateIoRestBalanceGateway
        gw = GateIoRestBalanceGateway.__new__(GateIoRestBalanceGateway)

        spot_assets = [{"coin": "USDT", "amount": 287.6}]
        futures_data = {"available": "287.6", "total": "287.6"}
        result = await gw._detect_unified_account("key", "secret", futures_data, spot_assets=spot_assets)
        self.assertTrue(result)

    async def test_detect_non_unified_with_different_amounts(self):
        from app.services.exchange_rest import GateIoRestBalanceGateway
        gw = GateIoRestBalanceGateway.__new__(GateIoRestBalanceGateway)

        # Futures 50, Spot 200 — very different, likely not unified
        spot_assets = [{"coin": "USDT", "amount": 200.0}]
        futures_data = {"available": "50", "total": "50"}
        result = await gw._detect_unified_account("key", "secret", futures_data, spot_assets=spot_assets)
        # A non-matching futures balance is a separate account, not a mirror.
        self.assertFalse(result)

    async def test_detect_no_futures_balance(self):
        from app.services.exchange_rest import GateIoRestBalanceGateway
        gw = GateIoRestBalanceGateway.__new__(GateIoRestBalanceGateway)

        futures_data = {"available": "0", "total": "0"}
        result = await gw._detect_unified_account("key", "secret", futures_data, spot_assets=[])
        self.assertFalse(result)



# ============================================================
# 2. Display currency / FX rates
# ============================================================

class TestFxRates(unittest.TestCase):
    def test_convert_usd_to_rub(self):
        from bot.services.fx_rates import convert_usd
        rates = {"USD": 1.0, "RUB": 92.0, "EUR": 0.92}
        self.assertAlmostEqual(convert_usd(100.0, "RUB", rates), 9200.0)

    def test_convert_usd_to_eur(self):
        from bot.services.fx_rates import convert_usd
        rates = {"USD": 1.0, "EUR": 0.92}
        self.assertAlmostEqual(convert_usd(100.0, "EUR", rates), 92.0)

    def test_convert_usd_to_usd(self):
        from bot.services.fx_rates import convert_usd
        rates = {"USD": 1.0}
        self.assertAlmostEqual(convert_usd(100.0, "USD", rates), 100.0)

    def test_format_fiat_rub(self):
        from bot.services.fx_rates import format_fiat
        result = format_fiat(9200.0, "RUB")
        self.assertIn("₽", result)

    def test_format_fiat_eur(self):
        from bot.services.fx_rates import format_fiat
        result = format_fiat(92.0, "EUR")
        self.assertIn("€", result)

    def test_format_fiat_usd(self):
        from bot.services.fx_rates import format_fiat
        result = format_fiat(100.0, "USD")
        self.assertIn("$", result)

    def test_format_fiat_large_number(self):
        from bot.services.fx_rates import format_fiat
        result = format_fiat(100000.0, "RUB")
        self.assertIn("₽", result)
        self.assertIn(",", result)  # comma-separated thousands

    def test_currency_codes_contain_expected(self):
        from bot.services.fx_rates import currency_codes
        codes = currency_codes()
        for expected in ("USD", "EUR", "RUB", "UAH", "BYN", "KZT", "CNY", "TRY", "GBP"):
            self.assertIn(expected, codes)

    def test_currency_label_en(self):
        from bot.services.fx_rates import currency_label
        label = currency_label("RUB", "en")
        self.assertIn("RUB", label)
        self.assertIn("₽", label)

    def test_currency_label_ru(self):
        from bot.services.fx_rates import currency_label
        label = currency_label("RUB", "ru")
        self.assertIn("Российский рубль", label)

    def test_supported_currencies_has_symbols(self):
        from bot.services.fx_rates import SUPPORTED_CURRENCIES
        for code, info in SUPPORTED_CURRENCIES.items():
            self.assertIn("symbol", info, f"{code} missing symbol")
            self.assertIn("name_en", info, f"{code} missing name_en")
            self.assertIn("name_ru", info, f"{code} missing name_ru")

    def test_fallback_rates_for_all_currencies(self):
        from bot.services.fx_rates import _FALLBACK_RATES, SUPPORTED_CURRENCIES
        for code in SUPPORTED_CURRENCIES:
            self.assertIn(code, _FALLBACK_RATES, f"Missing fallback rate for {code}")


# ============================================================
# 3. Tariff payment-only
# ============================================================

class TestTariffPaymentOnly(unittest.TestCase):
    def test_plan_screen_shows_price(self):
        from bot.keyboards.inline import plan_screen
        plans = [
            {"code": "free", "name": "Free", "price_monthly": 0},
            {"code": "pro", "name": "Pro", "price_monthly": 9.99},
        ]
        kb = plan_screen(rev=1, locale="ru", plans=plans, current_plan_code="free")
        # Find the pro button text
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        pro_btn = next((t for t in texts if "Pro" in t), None)
        self.assertIsNotNone(pro_btn)
        self.assertIn("9.99", pro_btn)  # Price should be shown

    def test_current_plan_shows_current_label(self):
        from bot.keyboards.inline import plan_screen
        plans = [
            {"code": "free", "name": "Free", "price_monthly": 0},
        ]
        kb = plan_screen(rev=1, locale="ru", plans=plans, current_plan_code="free")
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        free_btn = next((t for t in texts if "Free" in t), None)
        self.assertIsNotNone(free_btn)


# ============================================================
# 4. Notifications stub
# ============================================================

class TestNotificationsStub(unittest.TestCase):
    def test_i18n_has_notifications_strings(self):
        from bot.i18n import _MESSAGES
        for locale in ("en", "ru"):
            self.assertIn("notifications", _MESSAGES.get(locale, {}))
            self.assertIn("notifications_in_dev", _MESSAGES.get(locale, {}))


    def test_ui_emoji_has_notifications(self):
        from bot.ui_emoji import UI_ICONS
        self.assertIn("notifications", UI_ICONS)

    def test_main_menu_has_notifications_button(self):
        from bot.keyboards.inline import main_menu
        kb = main_menu(rev=1, locale="ru", allow_dex=True, plan_label="Free", can_refresh=True, retry_after_seconds=0)
        all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        # Should contain a button with "Уведомления" or "Notifications"
        has_notif = any("Уведомл" in t or "Notif" in t for t in all_texts)
        self.assertTrue(has_notif, f"Notifications button not found in: {all_texts}")


# ============================================================
# 5. Dashboard trader metrics
# ============================================================

class TestDashboardMetricsSchema(unittest.TestCase):
    def test_dashboard_response_has_trader_fields(self):
        from app.schemas.balance import DashboardSummaryResponse
        resp = DashboardSummaryResponse(
            total_usd=1000.0,
            exchanges_count=1,
            spot_total=500.0,
            futures_total=300.0,
            dex_total=200.0,
            freshness="updated_just_now",
            plan={"code": "free"},
            capabilities={},
            throttling={},
            integrations={"total": 1, "active": 1},
            transactions_24h={"total": 0, "pending": 0, "failed": 0},
            timestamp=datetime.now(timezone.utc),
        )
        # Trader metric fields default to "unknown" and never invent a value.
        self.assertIsNone(resp.pnl_today)
        self.assertIsNone(resp.pnl_today_pct)
        self.assertIsNone(resp.pnl_24h)
        self.assertIsNone(resp.pnl_7d)
        self.assertIsNone(resp.pnl_30d)
        self.assertIsNone(resp.balance_today_start)
        self.assertIsNone(resp.balance_24h_ago)
        self.assertIsNone(resp.balance_7d_ago)
        self.assertIsNone(resp.balance_30d_ago)
        self.assertFalse(resp.pnl_includes_transfers)

    def test_dashboard_response_with_metrics(self):
        from app.schemas.balance import DashboardSummaryResponse
        resp = DashboardSummaryResponse(
            total_usd=1000.0,
            exchanges_count=1,
            spot_total=500.0,
            futures_total=300.0,
            dex_total=200.0,
            freshness="updated_just_now",
            plan={"code": "free"},
            capabilities={},
            throttling={},
            integrations={"total": 1, "active": 1},
            transactions_24h={"total": 0, "pending": 0, "failed": 0},
            timestamp=datetime.now(timezone.utc),
            pnl_today=25.0,
            pnl_today_pct=2.6,
            pnl_24h=50.5,
            pnl_24h_pct=5.3,
            pnl_7d=-20.0,
            pnl_7d_pct=-1.9,
            pnl_30d=100.0,
            pnl_30d_pct=11.1,
            balance_24h_ago=975.0,
            pnl_includes_transfers=True,
        )
        self.assertEqual(resp.pnl_today, 25.0)
        self.assertEqual(resp.pnl_24h, 50.5)
        self.assertEqual(resp.pnl_7d, -20.0)
        self.assertEqual(resp.pnl_30d, 100.0)
        self.assertEqual(resp.balance_24h_ago, 975.0)
        self.assertTrue(resp.pnl_includes_transfers)


class TestDashboardText(unittest.TestCase):
    def _get_text(self, result):
        """Extract plain text from aiogram Text object."""
        kwargs = result.as_kwargs()
        return kwargs.get("text", "")

    def test_dashboard_text_without_metrics(self):
        from bot.messages import dashboard_text
        summary = {
            "total_usd": 1000.0,
            "exchanges_count": 2,
            "spot_total": 600.0,
            "futures_total": 200.0,
            "dex_total": 200.0,
            "plan": {"name": "Free", "code": "free"},
            "capabilities": {"can_refresh": True},
            "throttling": {"retry_after_seconds": 0},
            "integrations": {"total": 1, "active": 1},
            "freshness": "updated_just_now",
        }
        result = dashboard_text(summary, locale="ru")
        text = self._get_text(result)
        self.assertIn("$1,000.00", text)  # Total
        self.assertIn("Free", text)  # Plan

    def test_dashboard_text_with_currency_label(self):
        from bot.messages import dashboard_text
        summary = {
            "total_usd": 1000.0,
            "exchanges_count": 1,
            "spot_total": 500.0,
            "futures_total": 300.0,
            "dex_total": 200.0,
            "plan": {"name": "Free", "code": "free"},
            "capabilities": {"can_refresh": True},
            "throttling": {"retry_after_seconds": 0},
            "integrations": {"total": 1, "active": 1},
            "freshness": "updated_just_now",
        }
        result = dashboard_text(summary, locale="ru", currency_label="₽92,000.00")
        text = self._get_text(result)
        self.assertIn("Доп. валюта", text)
        self.assertIn("₽92,000.00", text)

    def test_dashboard_text_with_pnl_metrics(self):
        from bot.messages import dashboard_text
        summary = {
            "total_usd": 1000.0,
            "exchanges_count": 1,
            "spot_total": 500.0,
            "futures_total": 300.0,
            "dex_total": 200.0,
            "plan": {"name": "Free", "code": "free"},
            "capabilities": {"can_refresh": True},
            "throttling": {"retry_after_seconds": 0},
            "integrations": {"total": 1, "active": 1},
            "freshness": "updated_just_now",
            "pnl_today": 50.0,
            "pnl_today_pct": 5.0,
            "pnl_7d": 0.0,
            "pnl_30d": -25.0,
            "pnl_30d_pct": -2.5,
            "avg_daily_pnl": 10.0,
        }
        result = dashboard_text(summary, locale="ru")
        text = self._get_text(result)
        # Should contain PnL metrics section
        self.assertIn("PnL сегодня", text)
        self.assertIn("+$50.00", text)
        self.assertIn("5.0%", text)
        self.assertIn("PnL 30д", text)
        self.assertIn("$-25.00", text)
        self.assertNotIn("PnL 7д", text)
        self.assertNotIn("Средний PnL/день", text)



# ============================================================
# Cross-cutting: CryptoBot integration + settings
# ============================================================

class TestCryptoBotSchemaValidation(unittest.TestCase):
    def test_cryptobot_auto_fills_exchange_code(self):
        from app.schemas.integration import IntegrationCreateRequest
        req = IntegrationCreateRequest(
            provider="cryptobot",
            name="My CryptoBot",
            kind="cex",
            account_ref="main",
            api_token="test-token",
        )
        self.assertEqual(req.exchange_code, "cryptobot")

    def test_cryptobot_apps_auto_fills_exchange_code(self):
        from app.schemas.integration import IntegrationCreateRequest
        req = IntegrationCreateRequest(
            provider="cryptobot_apps",
            name="My CryptoBot Apps",
            kind="cex",
            account_ref="main",
            exchange_code="cryptobot",
            api_token="test-token",
        )
        self.assertEqual(req.exchange_code, "cryptobot")


class TestSettingsCurrencyIntegration(unittest.TestCase):
    def _get_text(self, result):
        """Extract plain text from aiogram Text object."""
        kwargs = result.as_kwargs()
        return kwargs.get("text", "")

    def test_settings_shows_currency(self):
        from bot.messages import settings_text
        result = settings_text(
            hide_small=True,
            threshold=1.0,
            language="ru",
            locale="ru",
            display_currency="RUB",
        )
        text = self._get_text(result)
        self.assertIn("RUB", text)


    def test_settings_keyboard_has_currency_button(self):
        from bot.keyboards.inline import settings
        kb = settings(
            hide_small=True,
            language="ru",
            rev=1,
            locale="ru",
            display_currency="RUB",
        )
        texts = [btn.text for row in kb.inline_keyboard for btn in row]
        has_currency = any("RUB" in t or "валют" in t.lower() for t in texts)
        self.assertTrue(has_currency, f"Currency button not found in: {texts}")

    def test_runtime_settings_preserve_display_currency(self):
        from bot.services.runtime import _sanitize_settings

        payload = _sanitize_settings({"display_currency": "rub"})
        self.assertEqual(payload["display_currency"], "RUB")

    def test_runtime_settings_reject_unknown_display_currency(self):
        from bot.services.runtime import _sanitize_settings

        payload = _sanitize_settings({"display_currency": "BAD"})
        self.assertEqual(payload["display_currency"], "USD")


if __name__ == "__main__":
    unittest.main()
