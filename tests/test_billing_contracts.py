import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.balance import Organization, Plan, Subscription, User
from app.routers.billing import billing_webhook, get_current_subscription, list_billing_plans, switch_subscription_plan
from app.schemas.billing import CreateInvoiceRequest, SwitchPlanRequest
from app.services.billing_service import BillingService
from app.services.crypto_bot_service import CryptoBotService
from app.services.ledger_service import LedgerService


class BillingContractsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _identity(self, session, user_id: int = 1001):
        org = Organization(name="Billing Org", slug="billing-org")
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return SimpleNamespace(
            organization=SimpleNamespace(id=org.id),
            user=SimpleNamespace(id=user_id),
        )

    async def test_plans_and_current_contracts(self):
        async with self.session_maker() as session:
            identity = await self._identity(session)
            plans = await list_billing_plans(db=session, _=identity)
            self.assertTrue(plans.plans)
            self.assertEqual(
                {"code", "name", "price_monthly", "currency", "policy", "is_active"},
                set(plans.plans[0].model_dump().keys()),
            )
            self.assertEqual({plan.code for plan in plans.plans}, {"free", "low", "medium", "pro"})

            current = await get_current_subscription(db=session, identity=identity)
            self.assertEqual(
                {"plan", "subscription", "policy", "limits", "throttling", "background", "usage", "capabilities", "wallet", "last_refresh_at"},
                set(current.model_dump().keys()),
            )
            self.assertIn("max_cex_accounts", current.limits)
            self.assertIn("max_evm_wallets", current.limits)
            self.assertIn("background_refresh_interval_seconds", current.throttling)
            self.assertIn("cex", current.usage)
            self.assertIn("evm", current.usage)

    async def test_switch_plan_contract(self):
        async with self.session_maker() as session:
            identity = await self._identity(session)
            response = await switch_subscription_plan(
                payload=SwitchPlanRequest(plan_code="pro"),
                db=session,
                identity=identity,
            )
            self.assertEqual(
                {"status", "changed", "plan_code"},
                set(response.model_dump().keys()),
            )
            self.assertEqual(response.plan_code, "pro")

    async def test_downgrade_preserves_period_end(self):
        async with self.session_maker() as session:
            org = Organization(name="Downgrade Org", slug="downgrade-org")
            session.add(org)
            pro = Plan(code="pro", name="Pro", price_monthly=20.0, currency="USD", is_active=True)
            low = Plan(code="low", name="Low", price_monthly=5.0, currency="USD", is_active=True)
            session.add_all([pro, low])
            await session.flush()
            period_end = datetime.now(timezone.utc) + timedelta(days=12)
            session.add(
                Subscription(
                    organization_id=org.id,
                    plan_id=pro.id,
                    status="active",
                    current_period_end=period_end,
                )
            )
            await session.commit()

            changed = await BillingService(session).switch_subscription_plan(
                organization_id=org.id,
                plan_code="low",
            )
            self.assertTrue(changed)
            active = await BillingService(session).get_active_subscription(org.id)
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.plan_id, low.id)
            self.assertEqual(
                BillingService._normalize_datetime(active.current_period_end),
                period_end,
            )

    async def test_auto_renew_debits_balance_or_downgrades(self):
        async with self.session_maker() as session:
            org = Organization(name="Renew Org", slug="renew-org")
            session.add(org)
            free = Plan(code="free", name="Free", price_monthly=0.0, currency="USD", is_active=True)
            medium = Plan(code="medium", name="Medium", price_monthly=10.0, currency="USD", is_active=True)
            session.add_all([free, medium])
            await session.flush()
            expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            session.add(
                Subscription(
                    organization_id=org.id,
                    plan_id=medium.id,
                    status="active",
                    current_period_end=expired_at,
                )
            )
            await LedgerService(session).add_entry(
                organization_id=org.id,
                entry_type="credit",
                amount=10.0,
                source_type="test",
            )
            await session.commit()

            renewed = await BillingService(session).reconcile_subscription(org.id)
            self.assertEqual(renewed["action"], "renewed")
            self.assertAlmostEqual(await LedgerService(session).get_balance(org.id), 0.0)
            active = await BillingService(session).get_active_subscription(org.id)
            self.assertIsNotNone(active)
            assert active is not None
            assert active.current_period_end is not None
            self.assertGreater(
                BillingService._normalize_datetime(active.current_period_end),
                datetime.now(timezone.utc),
            )

            active.current_period_end = datetime.now(timezone.utc) - timedelta(minutes=1)
            await session.commit()
            downgraded = await BillingService(session).reconcile_subscription(org.id)
            self.assertEqual(downgraded["action"], "downgraded")
            active_after = await BillingService(session).get_active_subscription(org.id)
            self.assertIsNotNone(active_after)
            assert active_after is not None
            self.assertEqual(active_after.plan_id, free.id)

    async def test_plan_purchase_invoice_applies_subscription_once(self):
        async with self.session_maker() as session:
            org = Organization(name="Paid Org", slug="paid-org")
            user = User(email="paid@example.com", password_hash="hash", full_name="Paid User")
            session.add_all([org, user])
            await session.flush()
            free_plan = Plan(
                code="free",
                name="Free",
                max_integrations=6,
                min_refresh_interval_seconds=600,
                policy_json={
                    "version": 1,
                    "features": {"allow_dex": True},
                    "limits": {"max_cex_accounts": 5, "max_evm_wallets": 1},
                    "background": {"enabled": True, "refresh_interval_seconds": 600},
                    "throttling": {"min_refresh_interval_seconds": 600},
                },
                price_monthly=0.0,
                currency="USD",
                is_active=True,
            )
            session.add(free_plan)
            paid_plan = Plan(
                code="pro",
                name="Pro",
                max_integrations=56,
                min_refresh_interval_seconds=60,
                policy_json={
                    "version": 1,
                    "features": {"allow_dex": True},
                    "limits": {"max_cex_accounts": 56, "max_evm_wallets": 15},
                    "background": {"enabled": True, "refresh_interval_seconds": 60},
                    "throttling": {"min_refresh_interval_seconds": 60},
                },
                price_monthly=19.0,
                currency="USD",
                is_active=True,
            )
            session.add(paid_plan)
            await session.flush()
            session.add(Subscription(organization_id=org.id, plan_id=free_plan.id, status="active"))
            await session.commit()

            service = CryptoBotService(session)
            service._request = AsyncMock(
                return_value={
                    "invoice_id": "inv_123",
                    "status": "active",
                    "asset": "USDT",
                    "pay_url": "https://pay.test/inv_123",
                    "bot_invoice_url": "https://t.me/paybot/inv_123",
                    "expiration_date": "2026-03-20T10:00:00Z",
                }
            )
            invoice = await service.create_plan_purchase_invoice(
                organization_id=org.id,
                user_id=user.id,
                plan_code="pro",
            )
            self.assertEqual(invoice.invoice_type, "plan_purchase")
            self.assertEqual(invoice.amount, 19.0)
            self.assertEqual(invoice.metadata_json["plan_code"], "pro")

            webhook_payload = {
                "id": "evt_paid_1",
                "payload": {
                    "invoice_id": "inv_123",
                    "status": "paid",
                    "asset": "USDT",
                    "paid_amount": "19.00",
                    "paid_usd_rate": "1.0",
                    "expiration_date": "2026-03-20T10:00:00Z",
                    "paid_at": "2026-03-20T09:00:00Z",
                },
            }
            identity = SimpleNamespace(
                organization=SimpleNamespace(id=org.id),
                user=SimpleNamespace(id=user.id),
            )
            response = await billing_webhook(
                provider="cryptobot",
                payload=webhook_payload,
                x_event_id="evt_paid_1",
                db=session,
                identity=identity,
            )
            self.assertEqual(response["switched_plan"], True)
            self.assertEqual(response["plan_code"], "pro")

            active = await BillingService(session).get_active_subscription(org.id)
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.plan_id, paid_plan.id)
            self.assertIsNotNone(active.current_period_end)
            self.assertGreater(
                BillingService._normalize_datetime(active.current_period_end),
                datetime.now(timezone.utc),
            )

            repeat = await billing_webhook(
                provider="cryptobot",
                payload=webhook_payload,
                x_event_id="evt_paid_1",
                db=session,
                identity=identity,
            )
            self.assertEqual(repeat["switched_plan"], True)
            active_again = await BillingService(session).get_active_subscription(org.id)
            self.assertIsNotNone(active_again)
            assert active_again is not None
            self.assertEqual(active_again.plan_id, paid_plan.id)

    async def test_create_invoice_request_validation(self):
        topup = CreateInvoiceRequest(invoice_type="balance_topup", amount_usd=5.0)
        self.assertEqual(topup.invoice_type, "balance_topup")
        purchase = CreateInvoiceRequest(
            invoice_type="plan_purchase",
            plan_code="pro",
            amount_usd=19.0,
        )
        self.assertEqual(purchase.plan_code, "pro")
