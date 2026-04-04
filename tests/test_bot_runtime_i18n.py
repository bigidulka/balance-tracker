import unittest

from bot.i18n import locale_label, normalize_locale, t
from bot.services.runtime import _sanitize_settings


class BotI18nTests(unittest.TestCase):
    def test_runtime_settings_are_sanitized_for_language_and_filters(self):
        payload = _sanitize_settings(
            {
                "language": "EN",
                "hide_small": 1,
                "tx_type": "weird",
                "tx_status": "bad",
                "tx_since_hours": 999999,
            }
        )

        self.assertEqual(payload["language"], "en")
        self.assertTrue(payload["hide_small"])
        self.assertEqual(payload["tx_type"], "all")
        self.assertEqual(payload["tx_status"], "all")
        self.assertEqual(payload["tx_since_hours"], 24 * 30)

    def test_locale_helpers_fall_back_to_ru(self):
        self.assertEqual(normalize_locale("de"), "ru")
        self.assertEqual(locale_label("en"), "English")
        self.assertEqual(t("de", "settings"), "Настройки")
