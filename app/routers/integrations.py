import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import IdentityContext, require_role
from app.schemas.integration import (
    IntegrationCreateRequest,
    IntegrationRefreshResponse,
    IntegrationResponse,
    IntegrationUpdateRequest,
    IntegrationVerifyRequest,
    IntegrationVerifyResponse,
)
from app.services.audit_log_service import AuditLogService
from app.services.ccxt_manager import ccxt_manager
from app.services.integration_service import IntegrationService
from app.services.logging_context import get_request_logger, request_log_context
from app.services.metrics_service import metrics_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


def _to_response(integration) -> IntegrationResponse:
    return IntegrationResponse(
        id=integration.id,
        provider=integration.provider,
        name=integration.name,
        kind=integration.kind,
        exchange_code=integration.exchange_code,
        account_ref=integration.account_ref,
        wallet_address=integration.wallet_address,
        chain=integration.chain,
        is_active=integration.is_active,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
    )


@router.get("", response_model=list[IntegrationResponse])
async def list_integrations(
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("viewer")),
):
    service = IntegrationService(db)
    integrations = await service.list_integrations(
        identity.organization.id,
        include_inactive=include_inactive,
    )
    return [_to_response(integration) for integration in integrations]


@router.post("/verify", response_model=IntegrationVerifyResponse)
async def verify_integration_credentials(
    payload: IntegrationVerifyRequest,
    identity: IdentityContext = Depends(require_role("member")),
):
    exchange_code = payload.exchange_code.strip().lower()
    ok, error = await ccxt_manager.verify_credentials(
        exchange_id=exchange_code,
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        api_password=payload.api_password,
        api_uid=payload.api_uid,
    )
    return IntegrationVerifyResponse(ok=ok, error=error)


@router.post(
    "", response_model=IntegrationResponse, status_code=status.HTTP_201_CREATED
)
async def create_integration(
    payload: IntegrationCreateRequest,
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("member")),
):
    service = IntegrationService(db)
    integration = await service.create_integration(
        organization_id=identity.organization.id,
        provider=payload.provider,
        name=payload.name,
        kind=payload.kind,
        exchange_code=payload.exchange_code,
        account_ref=payload.account_ref,
        wallet_address=payload.wallet_address,
        chain=payload.chain,
        user_id=identity.user.id,
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        api_password=payload.api_password,
        api_uid=payload.api_uid,
    )

    request_id = f"integration-{integration.id}"
    await AuditLogService(db).log_event(
        organization_id=identity.organization.id,
        user_id=identity.user.id,
        request_id=request_id,
        action="integration.created",
        resource_type="integration",
        resource_id=str(integration.id),
        details={
            "provider": payload.provider,
            "name": payload.name,
            "kind": payload.kind,
            "exchange_code": payload.exchange_code,
            "account_ref": payload.account_ref,
            "wallet_address": payload.wallet_address,
            "chain": payload.chain,
        },
    )
    metrics_service.inc("integration_create_total")

    log = get_request_logger(
        logger,
        request_log_context(request_id, identity.organization.id, identity.user.id),
    )
    log.info("integration_created", extra={"integration_id": integration.id})

    return _to_response(integration)


@router.post("/{integration_id}/deactivate", response_model=IntegrationResponse)
async def deactivate_integration(
    integration_id: int,
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("member")),
):
    service = IntegrationService(db)
    integration = await service.deactivate_integration(
        organization_id=identity.organization.id,
        integration_id=integration_id,
    )

    request_id = f"integration-{integration.id}-deactivate"
    await AuditLogService(db).log_event(
        organization_id=identity.organization.id,
        user_id=identity.user.id,
        request_id=request_id,
        action="integration.deactivated",
        resource_type="integration",
        resource_id=str(integration.id),
        details={"integration_id": integration.id},
    )
    metrics_service.inc("integration_deactivate_total")

    log = get_request_logger(
        logger,
        request_log_context(request_id, identity.organization.id, identity.user.id),
    )
    log.info("integration_deactivated", extra={"integration_id": integration.id})

    return _to_response(integration)


@router.post("/{integration_id}/activate", response_model=IntegrationResponse)
async def activate_integration(
    integration_id: int,
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("member")),
):
    service = IntegrationService(db)
    integration = await service.activate_integration(
        organization_id=identity.organization.id,
        integration_id=integration_id,
    )

    request_id = f"integration-{integration.id}-activate"
    await AuditLogService(db).log_event(
        organization_id=identity.organization.id,
        user_id=identity.user.id,
        request_id=request_id,
        action="integration.activated",
        resource_type="integration",
        resource_id=str(integration.id),
        details={"integration_id": integration.id},
    )
    metrics_service.inc("integration_activate_total")

    log = get_request_logger(
        logger,
        request_log_context(request_id, identity.organization.id, identity.user.id),
    )
    log.info("integration_activated", extra={"integration_id": integration.id})

    return _to_response(integration)


@router.post("/{integration_id}/refresh", response_model=IntegrationRefreshResponse)
async def refresh_integration(
    integration_id: int,
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("member")),
):
    service = IntegrationService(db)
    integration, job_id, job_status = await service.queue_integration_refresh(
        organization_id=identity.organization.id,
        integration_id=integration_id,
    )

    request_id = f"integration-{integration.id}-refresh"
    await AuditLogService(db).log_event(
        organization_id=identity.organization.id,
        user_id=identity.user.id,
        request_id=request_id,
        action="integration.refresh_queued",
        resource_type="integration",
        resource_id=str(integration.id),
        details={
            "integration_id": integration.id,
            "job_id": job_id,
            "job_status": job_status,
        },
    )
    metrics_service.inc("integration_refresh_total")

    log = get_request_logger(
        logger,
        request_log_context(request_id, identity.organization.id, identity.user.id),
    )
    log.info(
        "integration_refresh_queued",
        extra={"integration_id": integration.id, "job_id": job_id},
    )

    return IntegrationRefreshResponse(
        status="queued",
        message="Integration refresh queued",
        integration_id=integration.id,
        job_id=job_id,
        job_status=job_status,
    )


@router.patch("/{integration_id}", response_model=IntegrationResponse)
async def update_integration(
    integration_id: int,
    payload: IntegrationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("member")),
):
    service = IntegrationService(db)
    integration = await service.update_integration(
        organization_id=identity.organization.id,
        integration_id=integration_id,
        name=payload.name,
    )

    request_id = f"integration-{integration.id}-rename"
    await AuditLogService(db).log_event(
        organization_id=identity.organization.id,
        user_id=identity.user.id,
        request_id=request_id,
        action="integration.renamed",
        resource_type="integration",
        resource_id=str(integration.id),
        details={"integration_id": integration.id, "name": integration.name},
    )

    log = get_request_logger(
        logger,
        request_log_context(request_id, identity.organization.id, identity.user.id),
    )
    log.info("integration_renamed", extra={"integration_id": integration.id})

    return _to_response(integration)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    integration_id: int,
    db: AsyncSession = Depends(get_db),
    identity: IdentityContext = Depends(require_role("member")),
):
    service = IntegrationService(db)
    await service.delete_integration(
        organization_id=identity.organization.id,
        integration_id=integration_id,
    )
