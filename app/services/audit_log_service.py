from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import AuditLog


class AuditLogService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log_event(
        self,
        organization_id: int,
        action: str,
        resource_type: str,
        details: dict[str, Any],
        request_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> AuditLog:
        entry = AuditLog(
            organization_id=organization_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            request_id=request_id,
        )
        self.session.add(entry)
        await self.session.commit()
        return entry
