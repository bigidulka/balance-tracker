import runpy
import unittest
from pathlib import Path


BUILD_OUTBOUND_PROXY_URL = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "probes" / "compare_ccxt_vs_rest.py")
)["_build_outbound_proxy_url"]


class OutboundProxyUrlTests(unittest.TestCase):
    def test_uses_credentials_only_when_username_is_present(self):
        self.assertEqual(
            BUILD_OUTBOUND_PROXY_URL({"host": "proxy.test", "port": 8080}),
            "http://proxy.test:8080",
        )
        self.assertEqual(
            BUILD_OUTBOUND_PROXY_URL(
                {
                    "host": "proxy.test",
                    "port": 8080,
                    "password": "password-without-user",
                }
            ),
            "http://proxy.test:8080",
        )
        self.assertEqual(
            BUILD_OUTBOUND_PROXY_URL(
                {
                    "host": "proxy.test",
                    "port": 8080,
                    "username": "user name",
                    "password": "p@ss/word",
                }
            ),
            "http://user%20name:p%40ss%2Fword@proxy.test:8080",
        )
