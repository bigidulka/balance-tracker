import unittest
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.balance import Integration, Organization, OrganizationMembership, Plan, Subscription, TelegramIdentity, User
from app.repositories.admin import AdminRepository
from app.routers.billing import get_current_subscription
from app.services.ledger_service import LedgerService
from app.services.promo_service import PromoService


class BillingWalletAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_promo_credit_updates_wallet_balance(self):
        async with self.session_maker() as session:
            org = Organization(name="Promo Org", slug="promo-org")
            user = User(email="promo@example.com", password_hash="hash", full_name="Promo User")
            session.add_all([org, user])
            await session.flush()
            session.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role="owner"))
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
            await session.flush()
            session.add(Subscription(organization_id=org.id, plan_id=free_plan.id, status="active"))
            await session.commit()

            promo = await PromoService(session).create_promo_code(
                code="WELCOME10",
                reward_type="balance_credit",
                reward_value=10.0,
            )
            redemption = await PromoService(session).redeem_code(
                organization_id=org.id,
                user_id=user.id,
                raw_code=promo.code,
            )
            self.assertEqual(redemption.status, "applied")
            balance = await LedgerService(session).get_balance(org.id)
            self.assertEqual(balance, 10.0)

            identity = SimpleNamespace(
                organization=SimpleNamespace(id=org.id),
                user=SimpleNamespace(id=user.id),
                membership=SimpleNamespace(role="owner"),
            )
            payload = await get_current_subscription(db=session, identity=identity)
            dumped = payload.model_dump()
            self.assertIn("wallet", dumped)
            self.assertEqual(dumped["wallet"]["available"], 10.0)

    async def test_admin_repository_returns_pagination_and_detail(self):
        async with self.session_maker() as session:
            org = Organization(name="Admin Org", slug="admin-org")
            user = User(email="admin@example.com", password_hash="hash", full_name="Admin User")
            session.add_all([org, user])
            await session.flush()
            session.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role="admin"))
            plan = Plan(
                code="medium",
                name="Medium",
                max_integrations=10,
                min_refresh_interval_seconds=120,
                policy_json={
                    "version": 1,
                    "features": {"allow_dex": True},
                    "limits": {"max_cex_accounts": 28, "max_evm_wallets": 7},
                    "background": {"enabled": True, "refresh_interval_seconds": 120},
                    "throttling": {"min_refresh_interval_seconds": 120},
                },
                price_monthly=29.0,
                currency="USD",
                is_active=True,
            )
            session.add(plan)
            await session.flush()
            session.add(Subscription(organization_id=org.id, plan_id=plan.id, status="active"))
            session.add(
                Integration(
                    organization_id=org.id,
                    provider="binance",
                    name="Binance Main",
                    kind="cex",
                    exchange_code="binance",
                    account_ref="main",
                    is_active=True,
                )
            )
            session.add(
                TelegramIdentity(
                    telegram_user_id=6238100241,
                    telegram_username="***REMOVED***_admin",
                    telegram_first_name="Arbitron",
                    telegram_last_name="Admin",
                    telegram_full_name="Arbitron Admin",
                    user_id=user.id,
                    organization_id=org.id,
                )
            )
            await LedgerService(session).add_entry(
                organization_id=org.id,
                entry_type="credit",
                amount=42.5,
                source_type="admin_adjustment",
                note="seed",
            )
            await session.commit()

            repo = AdminRepository(session)
            items, total = await repo.list_user_memberships(limit=10, offset=0)
            self.assertEqual(total, 1)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["plan_code"], "medium")
            self.assertEqual(items[0]["active_integrations"], 1)
            self.assertEqual(items[0]["balance_usd"], 42.5)
            self.assertEqual(items[0]["telegram_user_id"], 6238100241)
            self.assertEqual(items[0]["telegram_username"], "***REMOVED***_admin")

            detail = await repo.get_user_membership_detail(user_id=user.id, organization_id=org.id)
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["organization_name"], "Admin Org")
            self.assertEqual(len(detail["integrations"]), 1)
            self.assertEqual(detail["telegram_username"], "***REMOVED***_admin")
