import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.core.dependencies import get_current_organization_id, get_identity_context
from app.core.request_context import RequestContext


class APITokenAndTenantFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_context_requires_bearer_authorization(self):
        with self.assertRaises(HTTPException) as exc:
            await get_identity_context(authorization="", db=object())

        self.assertEqual(exc.exception.status_code, 401)
        self.assertIn("Authorization", str(exc.exception.detail))

    async def test_query_organization_id_override_is_used(self):
        identity = type(
            "Identity",
            (),
            {
                "user": type("UserObj", (), {"id": 1})(),
                "organization": type("OrgObj", (), {"id": 123})(),
                "membership": type("MembershipObj", (), {"role": "viewer"})(),
            },
        )()

        with patch("app.core.dependencies.AuthService.resolve_identity", new_callable=AsyncMock) as resolve:
            resolve.return_value = identity

            result = await get_identity_context(
                authorization="Bearer token",
                x_organization_id=None,
                organization_id=123,
                db=object(),
            )

        self.assertEqual(result.organization.id, 123)
        self.assertEqual(resolve.await_args.kwargs.get("organization_override"), 123)

    async def test_header_organization_id_takes_precedence_over_query(self):
        identity = type(
            "Identity",
            (),
            {
                "user": type("UserObj", (), {"id": 1})(),
                "organization": type("OrgObj", (), {"id": 777})(),
                "membership": type("MembershipObj", (), {"role": "viewer"})(),
            },
        )()

        with patch("app.core.dependencies.AuthService.resolve_identity", new_callable=AsyncMock) as resolve:
            resolve.return_value = identity

            result = await get_identity_context(
                authorization="Bearer token",
                x_organization_id=777,
                organization_id=123,
                db=object(),
            )

        self.assertEqual(result.organization.id, 777)
        self.assertEqual(resolve.await_args.kwargs.get("organization_override"), 777)

    async def test_get_current_organization_id_sets_request_context(self):
        request = type("Req", (), {})()
        request.state = type("State", (), {})()
        request.state.request_context = RequestContext(request_id="req-1", organization_id=0)

        identity = type(
            "Identity",
            (),
            {
                "organization": type("OrgObj", (), {"id": 66})(),
                "user": type("UserObj", (), {"id": 11})(),
            },
        )()

        org_id = await get_current_organization_id(request=request, identity=identity)

        self.assertEqual(org_id, 66)
        self.assertEqual(request.state.request_context.organization_id, 66)
        self.assertEqual(request.state.request_context.user_id, 11)
