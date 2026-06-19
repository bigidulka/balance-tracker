import unittest
from unittest.mock import AsyncMock, patch

from app.services.debank_sdk_client import DeBankSdkClient


class DeBankSdkClientTests(unittest.IsolatedAsyncioTestCase):
    def test_build_assets_normalizes_positive_tokens(self):
        assets = DeBankSdkClient._build_assets(
            [
                {
                    "symbol": "ETH",
                    "chain": "eth",
                    "amount": "1.25",
                    "amountUsd": "2500.5",
                },
                {
                    "symbol": "ZERO",
                    "chain": "base",
                    "amount": "0",
                    "amountUsd": "0",
                },
            ]
        )

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].coin, "ETH_eth")
        self.assertEqual(assets[0].amount, 1.25)
        self.assertEqual(assets[0].value_usd, 2500.5)

    async def test_fetch_wallet_balance_maps_response(self):
        client = DeBankSdkClient()
        with patch.object(
            client,
            "_request_json",
            new=AsyncMock(
                return_value={
                    "fetchedAt": 1710000000000,
                    "totalUsd": 50,
                    "tokens": [
                        {
                            "symbol": "USDC",
                            "chain": "base",
                            "amount": 50,
                            "amountUsd": 50,
                        }
                    ],
                }
            ),
        ):
            balance = await client.fetch_wallet_balance("0x463452C356322D463B84891eBDa33DAED274cB40")

        self.assertEqual(balance.service, "debank_sdk_0x463452")
        self.assertEqual(balance.total_usd, 50)
        self.assertEqual(balance.accounts[0].assets[0].coin, "USDC_base")

    async def test_fetch_wallet_balance_rejects_total_assets_mismatch(self):
        client = DeBankSdkClient()
        with patch.object(
            client,
            "_request_json",
            new=AsyncMock(
                return_value={
                    "fetchedAt": 1710000000000,
                    "totalUsd": 1000000,
                    "tokens": [
                        {
                            "symbol": "USDC",
                            "chain": "base",
                            "amount": 50,
                            "amountUsd": 50,
                        }
                    ],
                }
            ),
        ):
            with self.assertRaisesRegex(ValueError, "total/assets mismatch"):
                await client.fetch_wallet_balance("0x463452C356322D463B84891eBDa33DAED274cB40")

