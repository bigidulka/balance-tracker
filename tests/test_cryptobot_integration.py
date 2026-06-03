import unittest
from unittest.mock import AsyncMock, patch

from app.services.crypto_bot_app_client import CryptoBotAppClient, CryptoBotAppClientError


class CryptoBotAppClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_balance_maps_assets(self):
        client = CryptoBotAppClient()
        with patch.object(
            client,
            "get_balances",
            AsyncMock(
                return_value=[
                    {"currency_code": "TON", "available": "1.5", "onhold": "0.5", "usd_rate": "3"},
                    {"currency_code": "USDT", "available": "10", "onhold": "0", "usd_rate": "1"},
                ]
            ),
        ):
            balance = await client.fetch_balance("token", integration_id=7, service="cryptobot:7")

        self.assertEqual(balance.integration_id, 7)
        self.assertEqual(balance.service, "cryptobot:7")
        self.assertEqual(balance.total_usd, 16.0)
        self.assertEqual(len(balance.assets), 2)
        self.assertEqual(balance.assets[0].coin, "TON")
        self.assertEqual(balance.assets[0].amount, 2.0)
        self.assertEqual(balance.assets[0].value_usd, 6.0)

    async def test_get_balances_rejects_invalid_payload(self):
        client = CryptoBotAppClient()
        with patch.object(client, "_request", AsyncMock(return_value={"bad": True})):
            with self.assertRaises(CryptoBotAppClientError):
                await client.get_balances("token")
