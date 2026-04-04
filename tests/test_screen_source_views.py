import unittest

from bot.contracts.callbacks import ROUTE_DEX, ROUTE_TRANSACTIONS
from bot.services.screen_service import ScreenService


class _SourceViewApiRepo:
    async def get_balances(self):
        return {
            "services": [
                {
                    "service": "binance",
                    "accounts": [
                        {
                            "account_type": "spot",
                            "total_usd": 120.0,
                            "assets": [
                                {"coin": "USDT", "amount": 120.0, "value_usd": 120.0},
                            ],
                        }
                    ],
                    "assets": [],
                    "total_usd": 120.0,
                    "updated_at": "2026-03-21T10:00:00+00:00",
                },
                {
                    "service": "okx_wallet_0xf9095877f93603d0b6c44e5a82db5dc751b34cd8",
                    "accounts": [
                        {
                            "account_type": "spot",
                            "total_usd": 55.5,
                            "assets": [
                                {"coin": "USDC", "amount": 50.0, "value_usd": 50.0},
                                {"coin": "ETH", "amount": 0.002, "value_usd": 5.5},
                            ],
                        }
                    ],
                    "assets": [],
                    "total_usd": 55.5,
                    "updated_at": "2026-03-21T10:00:00+00:00",
                },
            ]
        }

    async def get_integrations(self):
        return [
            {
                "id": 1,
                "name": "Binance main",
                "provider": "ccxt",
                "kind": "cex",
                "exchange_code": "binance",
                "is_active": True,
            },
            {
                "id": 2,
                "name": "EVM 0xf909...4cd8",
                "provider": "okx_wallet",
                "kind": "dex",
                "wallet_address": "0xf9095877f93603d0b6c44e5a82db5dc751b34cd8",
                "is_active": True,
            },
        ]

    async def get_transactions(self, **kwargs):
        service = kwargs.get("tx_service")
        return {
            "service": service,
            "transactions": [
                {
                    "tx_type": "deposit",
                    "amount": 15.0,
                    "currency": "USDT",
                    "status": "ok",
                    "network": "TRC20",
                    "tx_timestamp": "2026-03-21T09:55:00+00:00",
                }
            ] if service else [],
            "total_count": 1 if service else 0,
            "refreshed_at": "2026-03-21T10:00:00+00:00",
        }


class _SourceViewUserRepo:
    async def get_settings(self, user_id: int):
        return {"language": "en", "hide_small": False, "tx_since_hours": 24}


class _PagedSourceViewApiRepo:
    async def get_balances(self):
        services = []
        for idx in range(9):
            services.append(
                {
                    "service": f"okx_wallet_0x{idx:040x}",
                    "accounts": [
                        {
                            "account_type": "spot",
                            "total_usd": float(100 - idx),
                            "assets": [{"coin": "USDC", "amount": 1.0, "value_usd": float(100 - idx)}],
                        }
                    ],
                    "assets": [],
                    "total_usd": float(100 - idx),
                    "updated_at": "2026-03-21T10:00:00+00:00",
                }
            )
        return {"services": services}

    async def get_integrations(self):
        return [
            {
                "id": idx + 1,
                "name": f"Wallet {idx}",
                "provider": "okx_wallet",
                "kind": "dex",
                "wallet_address": f"0x{idx:040x}",
                "is_active": True,
            }
            for idx in range(9)
        ]

    async def get_transactions(self, **kwargs):
        return {"transactions": [], "total_count": 0, "refreshed_at": "2026-03-21T10:00:00+00:00"}


class ScreenSourceViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_dex_route_renders_wallet_picker_and_detail(self):
        service = ScreenService(_SourceViewApiRepo(), _SourceViewUserRepo())

        picker = await service.render(route=ROUTE_DEX, payload={"page": 0}, user_id=1, rev=1)
        picker_buttons = [button.callback_data or "" for row in picker.keyboard.inline_keyboard for button in row]
        self.assertTrue(any("p=i%3D0%26page%3D0" in item for item in picker_buttons))

        detail = await service.render(
            route=ROUTE_DEX,
            payload={"i": 0, "page": 0},
            user_id=1,
            rev=1,
        )
        self.assertIn("0xf909...4cd8", detail.text.as_kwargs()["text"])
        detail_buttons = [button.callback_data or "" for row in detail.keyboard.inline_keyboard for button in row]
        self.assertTrue(any("p=i%3D0%26page%3D0" in item for item in detail_buttons))

    async def test_transactions_route_renders_source_picker_and_detail(self):
        service = ScreenService(_SourceViewApiRepo(), _SourceViewUserRepo())

        picker = await service.render(route=ROUTE_TRANSACTIONS, payload={"page": 0}, user_id=1, rev=1)
        picker_buttons = [button.callback_data or "" for row in picker.keyboard.inline_keyboard for button in row]
        self.assertTrue(any("p=i%3D0%26page%3D0" in item for item in picker_buttons))
        self.assertFalse(any("p=i%3D1%26page%3D0" in item for item in picker_buttons))

        detail = await service.render(
            route=ROUTE_TRANSACTIONS,
            payload={"i": 0, "page": 0},
            user_id=1,
            rev=1,
        )
        self.assertIn("Transactions", detail.text.as_kwargs()["text"])
        self.assertIn("deposit", detail.text.as_kwargs()["text"].lower())

    async def test_dex_picker_second_page_keeps_buttons(self):
        service = ScreenService(_PagedSourceViewApiRepo(), _SourceViewUserRepo())

        picker = await service.render(route=ROUTE_DEX, payload={"page": 1}, user_id=1, rev=1)
        picker_buttons = [button.text or "" for row in picker.keyboard.inline_keyboard for button in row]

        self.assertTrue(any(text.startswith("Wallet ") for text in picker_buttons))

    async def test_transaction_picker_second_page_keeps_buttons(self):
        service = ScreenService(_PagedSourceViewApiRepo(), _SourceViewUserRepo())

        picker = await service.render(route=ROUTE_TRANSACTIONS, payload={"page": 1}, user_id=1, rev=1)
        picker_buttons = [button.text or "" for row in picker.keyboard.inline_keyboard for button in row]

        self.assertFalse(any(text.startswith("0x") for text in picker_buttons))
