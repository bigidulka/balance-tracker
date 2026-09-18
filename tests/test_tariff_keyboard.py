import unittest

from bot.contracts.callbacks import (
    ACTION_INTEGRATION_DEACTIVATE,
    ROUTE_PLAN,
)
from bot.keyboards import inline as kb


class TariffKeyboardTests(unittest.TestCase):
    def test_settings_keyboard_does_not_include_plan_button(self):
        markup = kb.settings(
            hide_small=False, language="en", rev=1, locale="en", is_admin=False
        )
        payloads = [
            button.callback_data or ""
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertFalse(any(f"r={ROUTE_PLAN}" in item for item in payloads))

    def test_plan_keyboard_contains_plan_selection_buttons(self):
        markup = kb.plan_screen(
            rev=1,
            locale="en",
            current_plan_code="free",
            plans=[
                {"code": "free", "name": "Free"},
                {"code": "low", "name": "Low"},
            ],
        )
        texts = [button.text for row in markup.inline_keyboard for button in row]
        payloads = [
            button.callback_data or ""
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("Free · current", texts)
        # Plan buttons carry a price suffix (for example "Low · free"), so match the label prefix.
        self.assertTrue(any(text.startswith("Low") for text in texts), texts)
        self.assertTrue(any("plan_code%3Dlow" in item for item in payloads))

    def test_integration_actions_has_single_toggle_button(self):
        markup = kb.integration_actions(
            integration_id=17, is_active=True, rev=3, locale="en"
        )
        payloads = [
            button.callback_data or ""
            for row in markup.inline_keyboard
            for button in row
        ]
        texts = [button.text for row in markup.inline_keyboard for button in row]
        # exactly one deactivate/toggle button
        self.assertEqual(
            sum(f"a={ACTION_INTEGRATION_DEACTIVATE}" in item for item in payloads), 1
        )
        # delete button present
        self.assertTrue(any("a=ix" in item for item in payloads))
        # rename / input button present
        self.assertTrue(any("a=i" in item for item in payloads))
        # back button present
        self.assertTrue(any("a=b" in item for item in payloads))
