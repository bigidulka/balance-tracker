from typing import Optional
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.request_context import RequestContext

settings = get_settings()


def _parse_optional_int(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid4())
        organization_id = _parse_optional_int(request.headers.get("x-org-id"))
        user_id = _parse_optional_int(request.headers.get("x-user-id"))

        request.state.request_context = RequestContext(
            request_id=request_id,
            organization_id=organization_id or settings.default_org_id,
            user_id=user_id,
        )

        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
