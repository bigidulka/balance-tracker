from dataclasses import dataclass
from typing import Optional

from fastapi import Request


@dataclass(slots=True)
class RequestContext:
    request_id: str
    organization_id: int
    user_id: Optional[int] = None


async def get_request_context(request: Request) -> RequestContext:
    return request.state.request_context
