import unittest

from bot.services.screen_service import ScreenService


class _FakeApiRepo:
    def __init__(self) -> None:
        self.created_payloads: list[dict] = []
        self.refreshed_ids: list[int] = []
        self.verify_payloads: list[dict] = []

    async def create_integration(self, payload: dict) -> dict:
        self.created_payloads.append(dict(payload))
        return {"id": 91}

    async def refresh_integration(self, integration_id: int) -> dict:
        self.refreshed_ids.append(integration_id)
        return {"job_id": 301}

    async def verify_integration_credentials(self, payload: dict) -> dict:
        self.verify_payloads.append(dict(payload))
        return {"ok": True}


class _FakeUserRepo:
    async def get_settings(self, user_id: int) -> dict:
        return {"language": "en"}


class ScreenIntegrationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_cex_integration_collects_credentials_before_create(self):
        api_repo = _FakeApiRepo()
        service = ScreenService(api_repo, _FakeUserRepo())

        step1 = await service.process_input_value(
            user_id=1,
            waiting={
                "kind": "integration_cex_account_ref",
                "return_route": "ig",
                "return_payload": {},
                "draft": {
                    "provider": "ccxt",
                    "kind": "cex",
                    "exchange_code": "binance",
                },
            },
            raw_value="main",
        )
        self.assertTrue(step1.success)
        self.assertEqual(step1.next_waiting["kind"], "integration_cex_api_key")

        step2 = await service.process_input_value(
            user_id=1,
            waiting=step1.next_waiting,
            raw_value="api-key-1",
        )
        self.assertTrue(step2.success)
        self.assertEqual(step2.next_waiting["kind"], "integration_cex_api_secret")

        step3 = await service.process_input_value(
            user_id=1,
            waiting=step2.next_waiting,
            raw_value="api-secret-1",
        )
        self.assertTrue(step3.success)
        self.assertEqual(step3.next_waiting["kind"], "integration_cex_api_password")

        step4 = await service.process_input_value(
            user_id=1,
            waiting=step3.next_waiting,
            raw_value="-",
        )
        self.assertTrue(step4.success)
        self.assertEqual(step4.next_route, "id")
        self.assertEqual(step4.next_payload, {"id": 91, "job_id": 301})
        self.assertEqual(len(api_repo.created_payloads), 1)
        self.assertEqual(
            api_repo.created_payloads[0],
            {
                "provider": "ccxt",
                "kind": "cex",
                "exchange_code": "binance",
                "account_ref": "main",
                "name": "Binance main",
                "api_key": "api-key-1",
                "api_secret": "api-secret-1",
            },
        )
        self.assertEqual(api_repo.refreshed_ids, [91])

    async def test_evm_wallet_address_skips_chain_step_and_queues_refresh(self):
        api_repo = _FakeApiRepo()
        service = ScreenService(api_repo, _FakeUserRepo())

        result = await service.process_input_value(
            user_id=1,
            waiting={
                "kind": "integration_dex_wallet_address",
                "return_route": "ig",
                "return_payload": {},
                "draft": {"provider": "okx_wallet", "kind": "dex"},
            },
            raw_value="0xf9095877f93603d0b6c44e5a82db5dc751b34cd8",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.next_route, "id")
        self.assertEqual(result.next_payload, {"id": 91, "job_id": 301})
        self.assertEqual(len(api_repo.created_payloads), 1)
        self.assertEqual(api_repo.created_payloads[0]["chain"], "ethereum")
        self.assertEqual(api_repo.created_payloads[0]["name"], "ETH 0xf90…4cd8")

        self.assertEqual(api_repo.refreshed_ids, [91])

    async def test_cryptobot_flow_collects_token_before_create(self):
        api_repo = _FakeApiRepo()
        service = ScreenService(api_repo, _FakeUserRepo())

        step1 = await service.process_input_value(
            user_id=1,
            waiting={
                "kind": "integration_cex_account_ref",
                "return_route": "ig",
                "return_payload": {},
                "draft": {
                    "provider": "ccxt",
                    "kind": "cex",
                    "exchange_code": "cryptobot",
                },
            },
            raw_value="main",
        )
        self.assertTrue(step1.success)
        self.assertEqual(step1.next_waiting["kind"], "integration_cex_api_token")

        step2 = await service.process_input_value(
            user_id=1,
            waiting=step1.next_waiting,
            raw_value="cb-token-1",
        )
        self.assertTrue(step2.success)
        self.assertEqual(step2.next_route, "id")
        self.assertEqual(step2.next_payload, {"id": 91, "job_id": 301})
        self.assertEqual(
            api_repo.created_payloads[0],
            {
                "provider": "ccxt",
                "kind": "cex",
                "exchange_code": "cryptobot",
                "account_ref": "main",
                "name": "CryptoBot Apps main",
                "api_token": "cb-token-1",
            },
        )
        self.assertEqual(
            api_repo.verify_payloads,
            [{"exchange_code": "cryptobot", "api_token": "cb-token-1"}],
        )
        self.assertEqual(api_repo.refreshed_ids, [91])

    async def test_evm_wallet_limit_is_checked_before_create(self):
        api_repo = _FakeApiRepo()
        service = ScreenService(api_repo, _FakeUserRepo())

        async def _blocked(*, user_id: int, chain: str):
            return False, "EVM wallets: 1/1 limit reached"

        service.can_add_evm_wallet = _blocked  # type: ignore[method-assign]

        result = await service.process_input_value(
            user_id=1,
            waiting={
                "kind": "integration_dex_wallet_address",
                "return_route": "ig",
                "return_payload": {},
                "draft": {"provider": "okx_wallet", "kind": "dex"},
            },
            raw_value="0xf9095877f93603d0b6c44e5a82db5dc751b34cd8",
        )

        self.assertFalse(result.success)
        self.assertIn("1/1", result.error_text or "")
        self.assertEqual(api_repo.created_payloads, [])

    async def test_tron_address_autodetected_no_chain_step(self):
        api_repo = _FakeApiRepo()
        service = ScreenService(api_repo, _FakeUserRepo())

        result = await service.process_input_value(
            user_id=1,
            waiting={
                "kind": "integration_dex_wallet_address",
                "return_route": "ig",
                "return_payload": {},
                "draft": {"provider": "tron_ton", "kind": "dex"},
            },
            raw_value="TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.next_route, "id")
        self.assertEqual(len(api_repo.created_payloads), 1)
        self.assertEqual(api_repo.created_payloads[0]["chain"], "tron")
        self.assertEqual(api_repo.created_payloads[0]["provider"], "tron_ton")
        self.assertIsNone(result.next_waiting)

    async def test_ton_address_autodetected_no_chain_step(self):
        api_repo = _FakeApiRepo()
        service = ScreenService(api_repo, _FakeUserRepo())

        result = await service.process_input_value(
            user_id=1,
            waiting={
                "kind": "integration_dex_wallet_address",
                "return_route": "ig",
                "return_payload": {},
                "draft": {"provider": "tron_ton", "kind": "dex"},
            },
            raw_value="EQBvW8Z5huBkMJYdnfAEM5JqTNkuWX3diqYENkWsIL0XggGG",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.next_route, "id")
        self.assertEqual(len(api_repo.created_payloads), 1)
        self.assertEqual(api_repo.created_payloads[0]["chain"], "ton")
        self.assertEqual(api_repo.created_payloads[0]["provider"], "tron_ton")
        self.assertIsNone(result.next_waiting)

    async def test_solana_address_autodetected_no_chain_step(self):
        api_repo = _FakeApiRepo()
        service = ScreenService(api_repo, _FakeUserRepo())

        result = await service.process_input_value(
            user_id=1,
            waiting={
                "kind": "integration_dex_wallet_address",
                "return_route": "ig",
                "return_payload": {},
                "draft": {"provider": "okx_wallet", "kind": "dex"},
            },
            raw_value="9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.next_route, "id")
        self.assertEqual(len(api_repo.created_payloads), 1)
        self.assertEqual(api_repo.created_payloads[0]["chain"], "solana")
        self.assertEqual(api_repo.created_payloads[0]["provider"], "okx_wallet")
        self.assertIsNone(result.next_waiting)
