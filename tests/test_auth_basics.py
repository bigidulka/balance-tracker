import importlib
import os
import sys
import unittest


class BotAPIAuthConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._environ_backup = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._environ_backup)
        # Restore the modules in place: deleting them from sys.modules would hand other
        # test modules a different module object than the one they imported.
        self._reload_api_client()

    def _reload_api_client(self):
        """Re-read the environment into bot.config and bot.api_client without breaking identity."""

        if "bot.config" in sys.modules:
            importlib.reload(sys.modules["bot.config"])
        module = sys.modules.get("bot.api_client")
        if module is None:
            return importlib.import_module("bot.api_client")
        return importlib.reload(module)

    def test_build_headers_is_empty_without_per_user_session(self):
        os.environ["API_TOKEN"] = "test-token"
        os.environ["API_ORG_ID"] = "42"

        api_client_module = self._reload_api_client()
        client = api_client_module.APIClient()

        headers = client._build_headers()
        self.assertEqual(headers, {})

    def test_build_service_headers_include_bootstrap_credentials(self):
        os.environ["API_TOKEN"] = "test-token"
        os.environ["API_ORG_ID"] = "42"

        api_client_module = self._reload_api_client()
        client = api_client_module.APIClient()

        headers = client._build_service_headers()
        self.assertEqual(headers.get("Authorization"), "Bearer test-token")
        self.assertEqual(headers.get("X-Organization-Id"), "42")
        self.assertEqual(headers.get("X-Org-Id"), "42")

    def test_build_headers_is_empty_when_auth_not_configured(self):
        os.environ.pop("API_TOKEN", None)
        os.environ.pop("API_ORG_ID", None)

        api_client_module = self._reload_api_client()
        client = api_client_module.APIClient()

        headers = client._build_headers()
        self.assertEqual(headers, {})

    def test_add_org_param_preserves_existing_value(self):
        os.environ["API_ORG_ID"] = "42"

        api_client_module = self._reload_api_client()
        client = api_client_module.APIClient()

        params = client._add_org_param({"organization_id": "7", "limit": 10})
        self.assertEqual(params["organization_id"], "7")
        self.assertEqual(params["limit"], 10)

    def test_add_org_param_does_not_inject_shared_org_when_missing(self):
        os.environ["API_ORG_ID"] = "42"

        api_client_module = self._reload_api_client()
        client = api_client_module.APIClient()

        params = client._add_org_param({"limit": 10})
        self.assertNotIn("organization_id", params)
        self.assertEqual(params["limit"], 10)
