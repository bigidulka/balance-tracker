from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models.balance import Integration


@dataclass
class ProviderRefreshResult:
    status: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class IntegrationProvider(Protocol):
    provider_name: str

    async def refresh(
        self,
        organization_id: int,
        integration: Integration,
        payload: dict[str, Any] | None = None,
    ) -> ProviderRefreshResult:
        ...
