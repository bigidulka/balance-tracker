from fastapi import HTTPException
from sqlalchemy import and_, delete, select
from collections import defaultdict
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.balance import Integration, IntegrationSecret
from app.services.entitlements_service import EntitlementsService
from app.services.refresh_orchestrator import RefreshOrchestrator


class IntegrationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.entitlements = EntitlementsService(session)

    @staticmethod
    def _wallet_service_name(wallet_address: str, provider: str = "okx_wallet") -> str:
        from app.services.balance_service import wallet_service_name_for

        return wallet_service_name_for(wallet_address, provider)

    async def create_integration(
        self,
        organization_id: int,
        provider: str,
        name: str,
        kind: str,
        exchange_code: str | None,
        account_ref: str | None,
        wallet_address: str | None,
        chain: str | None,
        user_id: int | None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_password: str | None = None,
        api_uid: str | None = None,
        api_token: str | None = None,
    ) -> Integration:
        normalized_kind = kind.lower()
        normalized_exchange_code = exchange_code.lower() if exchange_code else None
        normalized_chain = chain.lower() if chain else None

        await self.entitlements.ensure_can_create_integration(
            organization_id,
            kind=normalized_kind,
            exchange_code=normalized_exchange_code,
            chain=normalized_chain,
        )

        duplicate_query = select(Integration).where(
            and_(
                Integration.organization_id == organization_id,
                Integration.is_active == True,
                Integration.kind == normalized_kind,
                (
                    and_(
                        Integration.exchange_code == normalized_exchange_code,
                        Integration.account_ref == account_ref,
                    )
                    if normalized_kind == "cex"
                    else and_(
                        Integration.wallet_address == wallet_address,
                        Integration.chain == normalized_chain,
                    )
                ),
            )
        )
        duplicate_result = await self.session.execute(duplicate_query)
        duplicate = duplicate_result.scalar_one_or_none()
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "integration_already_exists",
                    "message": "Integration with same identity already exists",
                },
            )

        normalized_provider = provider.lower().strip()

        integration = Integration(
            organization_id=organization_id,
            provider=normalized_provider,
            name=name,
            kind=normalized_kind,
            exchange_code=normalized_exchange_code,
            account_ref=account_ref,
            wallet_address=wallet_address,
            chain=normalized_chain,
            is_active=True,
            created_by_user_id=user_id,
        )
        self.session.add(integration)
        await self.session.flush()

        secret_pairs = {
            "api_key": api_key,
            "api_secret": api_secret,
            "api_password": api_password,
            "api_uid": api_uid,
            "api_token": api_token,
        }
        for key_name, secret_value in secret_pairs.items():
            value = str(secret_value or "").strip()
            if not value:
                continue
            self.session.add(
                IntegrationSecret(
                    integration_id=integration.id,
                    key_name=key_name,
                    secret_value=value,
                    is_encrypted=False,
                )
            )

        await self.session.commit()
        await self.session.refresh(integration)
        return integration

    async def get_integration_secrets(
        self,
        organization_id: int,
        integration_id: int,
    ) -> dict[str, str]:
        secrets_map = await self.get_integration_secrets_bulk(
            organization_id, [integration_id]
        )
        return secrets_map.get(integration_id, {})

    async def get_integration_secrets_bulk(
        self,
        organization_id: int,
        integration_ids: list[int],
    ) -> dict[int, dict[str, str]]:
        normalized_ids = sorted({int(value) for value in integration_ids if int(value) > 0})
        if not normalized_ids:
            return {}

        integration_rows = await self.session.execute(
            select(Integration.id).where(
                and_(
                    Integration.organization_id == organization_id,
                    Integration.id.in_(normalized_ids),
                )
            )
        )
        allowed_ids = {int(row[0]) for row in integration_rows.all()}
        if not allowed_ids:
            return {}

        result = await self.session.execute(
            select(IntegrationSecret).where(
                IntegrationSecret.integration_id.in_(allowed_ids)
            )
        )
        grouped: dict[int, dict[str, str]] = defaultdict(dict)
        for secret in result.scalars().all():
            grouped[int(secret.integration_id)][str(secret.key_name)] = str(secret.secret_value)
        return dict(grouped)

    async def list_integrations(
        self,
        organization_id: int,
        include_inactive: bool = False,
    ) -> list[Integration]:
        query = select(Integration).where(
            Integration.organization_id == organization_id
        )
        if not include_inactive:
            query = query.where(Integration.is_active == True)

        result = await self.session.execute(
            query.order_by(Integration.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_integration(
        self,
        organization_id: int,
        integration_id: int,
    ) -> Integration:
        result = await self.session.execute(
            select(Integration).where(
                and_(
                    Integration.id == integration_id,
                    Integration.organization_id == organization_id,
                )
            )
        )
        integration = result.scalar_one_or_none()
        if integration is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "integration_not_found",
                    "message": "Integration not found",
                },
            )
        return integration

    async def update_integration(
        self,
        organization_id: int,
        integration_id: int,
        name: str,
    ) -> Integration:
        name = name.strip()
        if not name:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_name",
                    "message": "Integration name must not be empty",
                },
            )
        integration = await self.get_integration(organization_id, integration_id)
        integration.name = name
        await self.session.commit()
        await self.session.refresh(integration)
        return integration

    async def deactivate_integration(
        self,
        organization_id: int,
        integration_id: int,
    ) -> Integration:
        integration = await self.get_integration(organization_id, integration_id)
        integration.is_active = False
        integration.status = "inactive"
        await self.session.commit()
        await self.session.refresh(integration)
        return integration

    async def delete_integration(
        self,
        organization_id: int,
        integration_id: int,
    ) -> None:
        integration = await self.get_integration(organization_id, integration_id)
        await self.session.execute(
            delete(IntegrationSecret).where(
                IntegrationSecret.integration_id == integration.id
            )
        )
        await self.session.delete(integration)
        await self.session.commit()

    async def activate_integration(
        self,
        organization_id: int,
        integration_id: int,
    ) -> Integration:
        integration = await self.get_integration(organization_id, integration_id)
        if not integration.is_active:
            await self.entitlements.ensure_can_create_integration(
                organization_id,
                kind=integration.kind,
                exchange_code=integration.exchange_code,
                chain=integration.chain,
            )
        integration.is_active = True
        integration.status = "active"
        await self.session.commit()
        await self.session.refresh(integration)
        return integration

    async def queue_integration_refresh(
        self,
        organization_id: int,
        integration_id: int,
    ) -> tuple[Integration, int, str]:
        integration = await self.get_integration(organization_id, integration_id)
        if not integration.is_active:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "integration_inactive",
                    "message": "Cannot refresh inactive integration",
                },
            )

        job = await RefreshOrchestrator(self.session).queue_integration_refresh(
            organization_id=organization_id,
            integration_id=integration.id,
            payload={"source": "manual_integration_refresh"},
        )
        return integration, job.id, job.status
