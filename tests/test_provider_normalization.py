import unittest

from app.services.ccxt_manager import CCXTManager, EXCHANGE_ACCOUNT_TYPES


class ProviderNormalizationSmokeTests(unittest.TestCase):
    def setUp(self):
        self.manager = CCXTManager()

    def test_exchange_account_type_contract_is_normalized(self):
        allowed = {"spot", "futures"}
        for exchange_id, account_types in EXCHANGE_ACCOUNT_TYPES.items():
            for item in account_types:
                self.assertIn(
                    item["type"],
                    allowed,
                    msg=f"Unexpected account type for {exchange_id}: {item['type']}",
                )

    def test_transaction_status_normalization_maps_common_values(self):
        base = {
            "id": "1",
            "currency": "USDT",
            "amount": 10,
            "timestamp": 1700000000000,
        }

        ok = self.manager._normalize_transaction({**base, "status": "completed"}, "binance", "deposit")
        pending = self.manager._normalize_transaction({**base, "status": "processing"}, "binance", "deposit")
        failed = self.manager._normalize_transaction({**base, "status": "error"}, "binance", "deposit")
        canceled = self.manager._normalize_transaction({**base, "status": "cancelled"}, "binance", "deposit")

        self.assertEqual(ok.status, "ok")
        self.assertEqual(pending.status, "pending")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(canceled.status, "canceled")

    def test_transaction_normalization_generates_fallback_id(self):
        tx = self.manager._normalize_transaction(
            {
                "txid": "onchain-hash",
                "currency": "ETH",
                "amount": 1.5,
                "status": "unknown",
                "timestamp": 1700000000000,
            },
            "okx",
            "withdrawal",
        )

        self.assertEqual(tx.tx_id, "onchain-hash")
        self.assertEqual(tx.status, "pending")
        self.assertEqual(tx.service, "okx")
        self.assertEqual(tx.tx_type, "withdrawal")
