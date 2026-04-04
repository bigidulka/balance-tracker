import unittest

from bot.services.screen_service import ScreenService


class _FakeApiRepo:
    def __init__(self) -> None:
        self.created_payloads: list[dict] = []
        self.refreshed_ids: list[int] = []

    async def create_integration(self, payload: dict) -> dict:
        self.created_payloads.append(dict(payload))
        return {"id": 91}

    async def refresh_integration(self, integration_id: int) -> dict:
        self.refreshed_ids.append(integration_id)
        return {"job_id": 301}


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
                "draft": {"provider": "ccxt", "kind": "cex", "exchange_code": "binance"},
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
        self.assertEqual(api_repo.created_payloads[0]["name"], "0xf909...4cd8")
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
