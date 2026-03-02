from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import Organization


class OrganizationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_active_organization_ids(self) -> list[int]:
        result = await self.session.execute(
            select(Organization.id).where(Organization.is_active == True).order_by(Organization.id)
        )
        return [row[0] for row in result.fetchall()]
