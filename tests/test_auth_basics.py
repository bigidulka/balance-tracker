import importlib
import os
import sys
import unittest


class BotAPIAuthConfigTests(unittest.TestCase):
    def _reload_api_client(self):
        for module_name in ["bot.api_client", "bot.config"]:
            if module_name in sys.modules:
                del sys.modules[module_name]
        return importlib.import_module("bot.api_client")

    def test_build_headers_includes_bearer_and_org_header_when_configured(self):
        os.environ["API_TOKEN"] = "test-token"
        os.environ["API_ORG_ID"] = "42"

        api_client_module = self._reload_api_client()
        client = api_client_module.APIClient()

        headers = client._build_headers()
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

    def test_add_org_param_sets_default_org_when_missing(self):
        os.environ["API_ORG_ID"] = "42"

        api_client_module = self._reload_api_client()
        client = api_client_module.APIClient()

        params = client._add_org_param({"limit": 10})
        self.assertEqual(params["organization_id"], "42")
        self.assertEqual(params["limit"], 10)
