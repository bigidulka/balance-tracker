import unittest

from bot.keyboards.inline import integrations
from bot.messages import integrations_text


class IntegrationHealthMenuTests(unittest.TestCase):
    def test_integration_list_and_keyboard_show_health_labels(self):
        items = [
            {
                "id": 1,
                "name": "OKX main",
                "kind": "cex",
                "provider": "ccxt",
                "exchange_code": "okx",
                "is_active": True,
                "health_status": "problem",
            },
            {
                "id": 2,
                "name": "Gate.io main",
                "kind": "cex",
                "provider": "ccxt",
                "exchange_code": "gateio",
                "is_active": True,
                "health_status": "warning",
            },
        ]

        text = integrations_text(items, locale="ru").as_kwargs()["text"]
        buttons = [
            button.text
            for row in integrations(items, rev=1, locale="ru").inline_keyboard
            for button in row
        ]

        self.assertIn("OKX main [CEX] - FAIL", text)
        self.assertIn("Gate.io main [CEX] - WARN", text)
        self.assertTrue(any("OKX main [FAIL]" in button for button in buttons))
        self.assertTrue(any("Gate.io main [WARN]" in button for button in buttons))
