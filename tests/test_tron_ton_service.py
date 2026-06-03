import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.balance import AssetSchema, ServiceBalanceSchema
from app.services.tron_ton_service import TronTonService


class TronTonAddressDetectionTests(unittest.TestCase):
    def test_tron_address_detected(self):
        self.assertTrue(
            TronTonService.is_tron_address("TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9")
        )

    def test_ton_address_detected(self):
        self.assertTrue(
            TronTonService.is_ton_address(
                "EQBvW8Z5huBkMJYdnfAEM5JqTNkuWX3diqYENkWsIL0XggGG"
            )
        )

    def test_evm_address_not_tron(self):
        self.assertFalse(
            TronTonService.is_tron_address("0x463452C356322D463B84891eBDa33DAED274cB40")
        )

    def test_solana_address_not_ton(self):
        self.assertFalse(
            TronTonService.is_ton_address(
                "52C9T2T7JRojtxumYnYZhyUmrN7kqzvCLc4Ksvjk7TxD"
            )
        )

    def test_empty_address_not_detected(self):
        self.assertFalse(TronTonService.is_tron_address(""))
        self.assertFalse(TronTonService.is_ton_address(""))


class TronTonServiceNameTests(unittest.TestCase):
    def test_service_name_lowercases(self):
        name = TronTonService.service_name_for("TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9")
        self.assertTrue(name.startswith("tron_ton_"))
        self.assertEqual(name, name.lower())


class TronBalanceParsingTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for TRON balance parsing — mock HTTP, no network."""

    def _trongrid_response(
        self,
        balance_sun: int = 10_000_000,
        trc20: list[dict] | None = None,
    ) -> dict:
        return {
            "success": True,
            "data": [
                {
                    "address": "TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9",
                    "balance": balance_sun,
                    "trc20": trc20 or [],
                }
            ],
            "meta": {},
        }

    async def test_trx_native_balance_converted_from_sun(self):
        service = TronTonService()
        trx_price = 0.30

        with (
            patch.object(service, "_get_session") as mock_session_factory,
            patch.object(
                service, "_get_price_usd", new=AsyncMock(return_value=trx_price)
            ),
        ):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = AsyncMock(
                return_value=self._trongrid_response(balance_sun=5_000_000)
            )
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=mock_resp)
            mock_session_factory.return_value = mock_session

            result = await service.fetch_tron_balance(
                "TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9"
            )

        self.assertEqual(len(result.assets), 1)
        trx_asset = result.assets[0]
        self.assertEqual(trx_asset.coin, "TRX")
        self.assertAlmostEqual(trx_asset.amount, 5.0)
        self.assertAlmostEqual(trx_asset.value_usd, 5.0 * trx_price)

    async def test_known_usdt_trc20_converted_with_6_decimals(self):
        service = TronTonService()
        usdt_contract = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

        trc20 = [{usdt_contract: "50000000"}]  # 50 USDT (6 decimals)

        with (
            patch.object(service, "_get_session") as mock_session_factory,
            patch.object(service, "_get_price_usd", new=AsyncMock(return_value=0.30)),
        ):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = AsyncMock(
                return_value=self._trongrid_response(balance_sun=0, trc20=trc20)
            )
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=mock_resp)
            mock_session_factory.return_value = mock_session

            result = await service.fetch_tron_balance(
                "TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9"
            )

        usdt_assets = [a for a in result.assets if a.coin == "USDT"]
        self.assertEqual(len(usdt_assets), 1)
        self.assertAlmostEqual(usdt_assets[0].amount, 50.0)
        self.assertAlmostEqual(usdt_assets[0].value_usd, 50.0)  # price fixed at 1.0

    async def test_unknown_trc20_included_at_zero_usd(self):
        service = TronTonService()
        unknown_contract = "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
        trc20 = [{unknown_contract: "1000000"}]

        with (
            patch.object(service, "_get_session") as mock_session_factory,
            patch.object(service, "_get_price_usd", new=AsyncMock(return_value=0.30)),
        ):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = AsyncMock(
                return_value=self._trongrid_response(balance_sun=0, trc20=trc20)
            )
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=mock_resp)
            mock_session_factory.return_value = mock_session

            result = await service.fetch_tron_balance(
                "TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9"
            )

        self.assertTrue(any(a.value_usd == 0.0 for a in result.assets))

    async def test_empty_account_data_returns_zero_balance(self):
        service = TronTonService()

        with patch.object(service, "_get_session") as mock_session_factory:
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = AsyncMock(
                return_value={"success": True, "data": [], "meta": {}}
            )
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=mock_resp)
            mock_session_factory.return_value = mock_session

            result = await service.fetch_tron_balance(
                "TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9"
            )

        self.assertEqual(result.assets, [])
        self.assertEqual(result.total_usd, 0.0)


class TonBalanceParsingTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for TON balance parsing — mock HTTP, no network."""

    def _tonapi_account_response(self, balance_nanoton: int = 2_000_000_000) -> dict:
        return {
            "address": "EQBvW8Z5huBkMJYdnfAEM5JqTNkuWX3diqYENkWsIL0XggGG",
            "balance": balance_nanoton,
            "status": "active",
        }

    def _tonapi_jettons_response(self) -> dict:
        return {
            "balances": [
                {
                    "balance": "100000000",  # 100 USDT (6 decimals)
                    "jetton": {
                        "symbol": "USDT",
                        "decimals": 6,
                        "address": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
                    },
                    "price": {"prices": {"USD": 1.0}},
                },
                {
                    "balance": "5000000000",  # 5 TON (9 decimals)
                    "jetton": {
                        "symbol": "SOME_TOKEN",
                        "decimals": 9,
                        "address": "EQAAAbcd",
                    },
                    "price": {},  # no price
                },
            ]
        }

    async def test_ton_native_balance_converted_from_nanoton(self):
        service = TronTonService()
        ton_price = 1.25

        with (
            patch.object(service, "_get_session") as mock_session_factory,
            patch.object(
                service, "_get_ton_native_price", new=AsyncMock(return_value=ton_price)
            ),
        ):
            # First call: account; second: jettons
            acc_resp = AsyncMock()
            acc_resp.raise_for_status = MagicMock()
            acc_resp.json = AsyncMock(
                return_value=self._tonapi_account_response(2_000_000_000)
            )
            acc_resp.__aenter__ = AsyncMock(return_value=acc_resp)
            acc_resp.__aexit__ = AsyncMock(return_value=False)

            jet_resp = AsyncMock()
            jet_resp.raise_for_status = MagicMock()
            jet_resp.json = AsyncMock(return_value={"balances": []})
            jet_resp.__aenter__ = AsyncMock(return_value=jet_resp)
            jet_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(side_effect=[acc_resp, jet_resp])
            mock_session_factory.return_value = mock_session

            result = await service.fetch_ton_balance(
                "EQBvW8Z5huBkMJYdnfAEM5JqTNkuWX3diqYENkWsIL0XggGG"
            )

        ton_assets = [a for a in result.assets if a.coin == "TON"]
        self.assertEqual(len(ton_assets), 1)
        self.assertAlmostEqual(ton_assets[0].amount, 2.0)
        self.assertAlmostEqual(ton_assets[0].value_usd, 2.0 * ton_price)

    async def test_jetton_with_price_included(self):
        service = TronTonService()

        with (
            patch.object(service, "_get_session") as mock_session_factory,
            patch.object(
                service, "_get_ton_native_price", new=AsyncMock(return_value=0.0)
            ),
        ):
            acc_resp = AsyncMock()
            acc_resp.raise_for_status = MagicMock()
            acc_resp.json = AsyncMock(return_value=self._tonapi_account_response(0))
            acc_resp.__aenter__ = AsyncMock(return_value=acc_resp)
            acc_resp.__aexit__ = AsyncMock(return_value=False)

            jet_resp = AsyncMock()
            jet_resp.raise_for_status = MagicMock()
            jet_resp.json = AsyncMock(return_value=self._tonapi_jettons_response())
            jet_resp.__aenter__ = AsyncMock(return_value=jet_resp)
            jet_resp.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(side_effect=[acc_resp, jet_resp])
            mock_session_factory.return_value = mock_session

            result = await service.fetch_ton_balance(
                "EQBvW8Z5huBkMJYdnfAEM5JqTNkuWX3diqYENkWsIL0XggGG"
            )

        symbols = {a.coin for a in result.assets}
        self.assertIn("USDT", symbols)
        self.assertIn("SOME_TOKEN", symbols)

        usdt = next(a for a in result.assets if a.coin == "USDT")
        self.assertAlmostEqual(usdt.amount, 100.0)
        self.assertAlmostEqual(usdt.value_usd, 100.0)

        some = next(a for a in result.assets if a.coin == "SOME_TOKEN")
        self.assertAlmostEqual(some.amount, 5.0)
        self.assertAlmostEqual(some.value_usd, 0.0)  # no price provided


class FetchWalletBalanceDispatchTests(unittest.IsolatedAsyncioTestCase):
    """Test that fetch_wallet_balance routes correctly to TRON vs TON."""

    async def test_routes_tron_address_to_fetch_tron_balance(self):
        service = TronTonService()
        expected = ServiceBalanceSchema(
            service="tron_ton_test",
            accounts=[],
            assets=[],
            total_usd=0.0,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

        with (
            patch.object(
                service, "fetch_tron_balance", new=AsyncMock(return_value=expected)
            ) as mock_tron,
            patch.object(service, "fetch_ton_balance", new=AsyncMock()) as mock_ton,
        ):
            await service.fetch_wallet_balance("TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9")

        mock_tron.assert_awaited_once_with("TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9")
        mock_ton.assert_not_called()

    async def test_routes_ton_address_to_fetch_ton_balance(self):
        service = TronTonService()
        expected = ServiceBalanceSchema(
            service="tron_ton_test",
            accounts=[],
            assets=[],
            total_usd=0.0,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

        with (
            patch.object(
                service, "fetch_ton_balance", new=AsyncMock(return_value=expected)
            ) as mock_ton,
            patch.object(service, "fetch_tron_balance", new=AsyncMock()) as mock_tron,
        ):
            await service.fetch_wallet_balance(
                "EQBvW8Z5huBkMJYdnfAEM5JqTNkuWX3diqYENkWsIL0XggGG"
            )

        mock_ton.assert_awaited_once_with(
            "EQBvW8Z5huBkMJYdnfAEM5JqTNkuWX3diqYENkWsIL0XggGG"
        )
        mock_tron.assert_not_called()

    async def test_rejects_unknown_address_format(self):
        service = TronTonService()

        with self.assertRaises(ValueError):
            await service.fetch_wallet_balance("not-a-valid-address")

    async def test_rejects_empty_address(self):
        service = TronTonService()

        with self.assertRaises(ValueError):
            await service.fetch_wallet_balance("")


class OkxWalletDelegationTests(unittest.IsolatedAsyncioTestCase):
    """Verify that OKXWalletService now delegates TON/TRON to tron_ton_service."""

    async def test_okx_wallet_delegates_tron_to_tron_ton_service(self):
        from app.services.okx_wallet import OKXWalletService
        from app.services import tron_ton_service as tron_ton_module

        service = OKXWalletService()
        expected = ServiceBalanceSchema(
            service="tron_ton_test",
            accounts=[],
            assets=[],
            total_usd=77.0,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

        with patch.object(
            tron_ton_module.tron_ton_service,
            "fetch_wallet_balance",
            new=AsyncMock(return_value=expected),
        ) as mock_delegate:
            result = await service.fetch_wallet_balance(
                "TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9"
            )

        self.assertEqual(result.total_usd, 77.0)
        mock_delegate.assert_awaited_once_with("TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9")

    async def test_okx_wallet_delegates_ton_to_tron_ton_service(self):
        from app.services.okx_wallet import OKXWalletService
        from app.services import tron_ton_service as tron_ton_module

        service = OKXWalletService()
        expected = ServiceBalanceSchema(
            service="tron_ton_test",
            accounts=[],
            assets=[],
            total_usd=33.0,
            updated_at=datetime.now(timezone.utc),
            actual=True,
        )

        with patch.object(
            tron_ton_module.tron_ton_service,
            "fetch_wallet_balance",
            new=AsyncMock(return_value=expected),
        ) as mock_delegate:
            result = await service.fetch_wallet_balance(
                "EQBvW8Z5huBkMJYdnfAEM5JqTNkuWX3diqYENkWsIL0XggGG"
            )

        self.assertEqual(result.total_usd, 33.0)
        mock_delegate.assert_awaited_once()
