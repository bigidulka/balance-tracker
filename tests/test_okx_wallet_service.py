import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.core.config import Settings
from app.schemas.balance import ServiceBalanceSchema
from app.services.okx_wallet import OKXWalletService


class OkxWalletSettingsTests(unittest.TestCase):
    def test_okx_wallet_targets_prefer_addresses(self):
        settings = Settings(
            okx_wallet_addresses='["0x463452C356322D463B84891eBDa33DAED274cB40"]',
            okx_wallet_account_ids='["LEGACY-ID"]',
        )

        self.assertEqual(
            settings.okx_wallet_targets,
            ["0x463452C356322D463B84891eBDa33DAED274cB40"],
        )

    def test_okx_wallet_targets_ignore_legacy_account_ids_by_default(self):
        settings = Settings(okx_wallet_addresses="[]", okx_wallet_account_ids='["LEGACY-ID"]')

        self.assertEqual(settings.okx_wallet_targets, [])

    def test_okx_wallet_targets_allow_legacy_account_ids_only_when_explicitly_enabled(self):
        settings = Settings(
            okx_wallet_addresses="[]",
            okx_wallet_account_ids='["LEGACY-ID"]',
            okx_wallet_legacy_enabled=True,
        )

        self.assertEqual(settings.okx_wallet_targets, ["LEGACY-ID"])


class OkxWalletServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_wallet_address_detection_supports_non_evm_formats(self):
        self.assertTrue(
            OKXWalletService.is_wallet_address(
                "52C9T2T7JRojtxumYnYZhyUmrN7kqzvCLc4Ksvjk7TxD"
            )
        )
        self.assertTrue(
            OKXWalletService.is_wallet_address(
                "EQAn0ZFItBslPs2y7eJlGX01YgzkXmv9hATJG4Ju8qzlHW7n"
            )
        )
        self.assertTrue(
            OKXWalletService.is_wallet_address(
                "TJZiWR7rgdviK7HHKTML3qrLGMbb98JYeb"
            )
        )

    async def test_candidate_chain_ids_use_solana_only(self):
        service = OKXWalletService()

        chain_ids = await service._get_candidate_chain_ids(
            "52C9T2T7JRojtxumYnYZhyUmrN7kqzvCLc4Ksvjk7TxD"
        )

        self.assertEqual(chain_ids, [501])

    async def test_candidate_chain_ids_reject_ton(self):
        service = OKXWalletService()

        with self.assertRaisesRegex(ValueError, "TON"):
            await service._get_candidate_chain_ids(
                "EQAn0ZFItBslPs2y7eJlGX01YgzkXmv9hATJG4Ju8qzlHW7n"
            )

    def test_build_priapi_wallet_payload_matches_frontend_shape(self):
        service = OKXWalletService()

        payload = service._build_priapi_wallet_payload(
            "0x463452C356322D463B84891eBDa33DAED274cB40",
            1,
        )

        self.assertEqual(payload["walletAddress"], "0x463452C356322D463B84891eBDa33DAED274cB40")
        self.assertEqual(payload["chainId"], 1)
        self.assertEqual(payload["limit"], 100)
        self.assertEqual(payload["orderBy"], "11")
        self.assertFalse(payload["filterStableOrNativeToken"])

    def test_parse_assets_supports_active_positions_payload(self):
        assets = OKXWalletService._parse_assets(
            [
                {
                    "tSym": "USDC",
                    "tAmt": "12.5",
                    "tCurAmt": "12.5",
                },
                {
                    "tSym": "USDC",
                    "tAmt": "7.5",
                    "tCurAmt": "7.5",
                },
            ]
        )

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].coin, "USDC")
        self.assertEqual(assets[0].amount, 20.0)
        self.assertEqual(assets[0].value_usd, 20.0)

    async def test_fetch_wallet_balance_uses_priapi_for_solana_wallets(self):
        service = OKXWalletService()
        expected = ServiceBalanceSchema(
            service=service.service_name_for("52C9T2T7JRojtxumYnYZhyUmrN7kqzvCLc4Ksvjk7TxD"),
            accounts=[],
            assets=[],
            total_usd=0.0,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

        with patch.object(
            service,
            "_fetch_priapi_wallet_balance",
            new=AsyncMock(return_value=expected),
        ) as priapi_mock, patch.object(
            service,
            "_fetch_evm_wallet_balance",
            new=AsyncMock(),
        ) as evm_mock:
            result = await service.fetch_wallet_balance(
                "52C9T2T7JRojtxumYnYZhyUmrN7kqzvCLc4Ksvjk7TxD"
            )

        self.assertEqual(result.service, expected.service)
        priapi_mock.assert_awaited_once()
        evm_mock.assert_not_called()

    async def test_fetch_wallet_balance_uses_debank_sdk_for_evm_wallets(self):
        service = OKXWalletService()
        expected = ServiceBalanceSchema(
            service=service.service_name_for("0x463452C356322D463B84891eBDa33DAED274cB40"),
            accounts=[],
            assets=[],
            total_usd=42.0,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

        with patch.object(
            service,
            "_fetch_evm_wallet_balance",
            new=AsyncMock(return_value=expected),
        ), patch.object(
            service,
            "_fetch_priapi_wallet_balance",
            new=AsyncMock(),
        ) as priapi_mock:
            result = await service.fetch_wallet_balance(
                "0x463452C356322D463B84891eBDa33DAED274cB40"
            )

        self.assertEqual(result.total_usd, 42.0)
        priapi_mock.assert_not_called()

    async def test_fetch_wallet_balance_rejects_legacy_account_ids(self):
        service = OKXWalletService()

        with self.assertRaisesRegex(ValueError, "archived"):
            await service.fetch_wallet_balance("LEGACY-ID")
