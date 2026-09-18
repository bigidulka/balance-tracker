import unittest
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from fastapi import HTTPException

from app.core.dependencies import IdentityContext, require_platform_admin
from app.core.database import Base
from app.models.balance import Organization, OrganizationMembership, TelegramIdentity, User
from app.routers.auth import router, telegram_bootstrap
from app.services.auth_service import AuthService
from app.repositories.auth import AuthRepository


class TelegramBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _service_identity(self, session):
        org = Organization(name="Service Org", slug="service-org")
        user = User(email="service@example.com", password_hash="hash", full_name="Service User")
        session.add_all([org, user])
        await session.flush()
        session.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role="owner"))
        await session.commit()
        return SimpleNamespace(
            organization=SimpleNamespace(id=org.id),
            user=SimpleNamespace(id=user.id),
            membership=SimpleNamespace(role="owner"),
        )

    async def test_bootstrap_creates_personal_org_and_identity(self):
        async with self.session_maker() as session:
            identity = await self._service_identity(session)
            payload = await telegram_bootstrap(
                payload=SimpleNamespace(
                    telegram_user_id=100200300,
                    telegram_username="balance_user",
                    telegram_first_name="Balance",
                    telegram_last_name="User",
                    telegram_full_name=None,
                ),
                db=session,
                _=identity,
            )

            self.assertEqual(payload.telegram_user_id, 100200300)
            self.assertEqual(payload.telegram_username, "balance_user")
            self.assertEqual(payload.role, "member")
            self.assertFalse(payload.is_platform_admin)
            self.assertTrue(payload.organization_name.startswith("Telegram "))
            self.assertTrue(payload.access_token)

            repo = AuthRepository(session)
            telegram_identity = await repo.get_telegram_identity(100200300)
            self.assertIsNotNone(telegram_identity)
            assert telegram_identity is not None
            self.assertEqual(telegram_identity.telegram_username, "balance_user")

            user = await repo.get_user_by_id(payload.user_id)
            self.assertIsNotNone(user)
            assert user is not None
            self.assertEqual(user.full_name, "Balance User")

            repeat = await telegram_bootstrap(
                payload=SimpleNamespace(
                    telegram_user_id=100200300,
                    telegram_username="balance_user_2",
                    telegram_first_name="Balance",
                    telegram_last_name="User",
                    telegram_full_name="Balance User",
                ),
                db=session,
                _=identity,
            )
            self.assertEqual(repeat.user_id, payload.user_id)
            self.assertEqual(repeat.organization_id, payload.organization_id)
            updated_identity = await repo.get_telegram_identity(100200300)
            assert updated_identity is not None
            self.assertEqual(updated_identity.telegram_username, "balance_user_2")
            updated_user = await repo.get_user_by_id(payload.user_id)
            assert updated_user is not None
            self.assertEqual(updated_user.telegram_username, "balance_user_2")

    def test_bootstrap_routes_include_resolve_alias(self):
        paths = {route.path for route in router.routes}
        self.assertIn("/api/v1/auth/telegram/bootstrap", paths)
        self.assertIn("/api/v1/auth/telegram/resolve", paths)

    async def test_bootstrap_marks_admin_user_as_owner(self):
        async with self.session_maker() as session:
            identity = await self._service_identity(session)
            payload = await telegram_bootstrap(
                payload=SimpleNamespace(
                    telegram_user_id=6238100241,
                    telegram_username="test_admin",
                    telegram_first_name="Admin",
                    telegram_last_name="User",
                    telegram_full_name="Admin User",
                ),
                db=session,
                _=identity,
            )

            self.assertEqual(payload.role, "owner")
            self.assertTrue(payload.is_platform_admin)
            self.assertEqual(payload.telegram_username, "test_admin")
            self.assertTrue(payload.organization_slug.startswith("tg-"))

            bootstrap = await AuthService(AuthRepository(session)).bootstrap_telegram_identity(
                telegram_user_id=6238100241,
                telegram_username="test_admin_new",
                telegram_first_name="Admin",
                telegram_last_name="User",
                telegram_full_name="Admin User",
            )
            self.assertEqual(bootstrap.membership.role, "owner")
            self.assertEqual(bootstrap.telegram_identity.telegram_username, "test_admin_new")

            admin_identity = IdentityContext(
                user=bootstrap.user,
                organization=bootstrap.organization,
                membership=bootstrap.membership,
            )
            resolved = await require_platform_admin(identity=admin_identity)
            self.assertEqual(resolved.user.id, bootstrap.user.id)

            repo = AuthRepository(session)
            blocked_user = await repo.get_user_by_id(identity.user.id)
            blocked_org = await repo.get_organization_by_id(identity.organization.id)
            assert blocked_user is not None
            assert blocked_org is not None
            blocked_identity = IdentityContext(
                user=blocked_user,
                organization=blocked_org,
                membership=identity.membership,
            )
            with self.assertRaises(HTTPException):
                await require_platform_admin(identity=blocked_identity)

    async def test_bootstrap_recovers_partial_personal_org_without_identity(self):
        async with self.session_maker() as session:
            identity = await self._service_identity(session)
            partial_org = Organization(name="Telegram 555000111", slug="tg-555000111")
            partial_user = User(
                email="telegram-user-555000111@local.invalid",
                password_hash="!",
                full_name="Partial User",
            )
            session.add_all([partial_org, partial_user])
            await session.commit()

            payload = await telegram_bootstrap(
                payload=SimpleNamespace(
                    telegram_user_id=555000111,
                    telegram_username="partial_user",
                    telegram_first_name="Partial",
                    telegram_last_name="User",
                    telegram_full_name="Partial User",
                ),
                db=session,
                _=identity,
            )

            self.assertEqual(payload.organization_id, partial_org.id)
            self.assertEqual(payload.user_id, partial_user.id)
            self.assertEqual(payload.telegram_username, "partial_user")

            repo = AuthRepository(session)
            telegram_identity = await repo.get_telegram_identity(555000111)
            assert telegram_identity is not None
            self.assertEqual(telegram_identity.organization_id, partial_org.id)
            self.assertEqual(telegram_identity.user_id, partial_user.id)
