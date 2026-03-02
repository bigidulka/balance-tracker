from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import SyncJob
from app.services.balance_service import BalanceService
from app.services.sync_job_service import SyncJobService


class RefreshOrchestrator:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def refresh_all_now(self, organization_id: int) -> tuple[list[str], list[str]]:
        service = BalanceService(self.session, organization_id=organization_id)
        return await service.refresh_all()

    async def queue_integration_refresh(
        self,
        organization_id: int,
        integration_id: int,
        payload: dict[str, Any] | None = None,
    ) -> SyncJob:
        service = SyncJobService(self.session)
        return await service.queue_refresh_for_integration(
            organization_id=organization_id,
            integration_id=integration_id,
            payload=payload,
        )

    async def run_next_job(self, organization_id: int | None = None) -> SyncJob | None:
        service = SyncJobService(self.session)
        return await service.run_next_job(organization_id=organization_id)
