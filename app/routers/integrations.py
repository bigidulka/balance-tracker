import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import IdentityContext, require_role
from app.models.balance import ServiceStatus, SyncJob
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
from app.services.integrations.create_validation import (
    CRYPTOBOT_PROVIDER_CODES,
    validate_integration_create_payload,
)
from app.services.crypto_bot_app_client import crypto_bot_app_client
from app.services.logging_context import get_request_logger, request_log_context
from app.services.integration_keys import service_key_for_integration
from app.services.metrics_service import metrics_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


def _service_key_for_integration(integration) -> str:
    return service_key_for_integration(integration)


def _health_status(integration, service_status=None, latest_job=None) -> str:
    if not integration.is_active:
        return "inactive"

    last_job_status = str(getattr(latest_job, "status", "") or "").lower()
    latest_failed = last_job_status == "failed"
    service_unhealthy = (
        service_status is not None
        and getattr(service_status, "is_healthy", None) is False
    )
    never_synced = integration.last_synced_at is None

    if service_unhealthy or (latest_failed and never_synced):
        return "problem"
    if latest_failed:
        return "warning"
    if never_synced:
        return "unknown"
    return "ok"


async def _service_statuses_by_key(
    db: AsyncSession, organization_id: int, integrations
) -> dict[str, ServiceStatus]:
    service_keys = {
        _service_key_for_integration(integration)
        for integration in integrations
        if _service_key_for_integration(integration)
    }
    if not service_keys:
        return {}
    result = await db.execute(
        select(ServiceStatus).where(
            ServiceStatus.organization_id == organization_id,
            ServiceStatus.service.in_(service_keys),
        )
    )
    return {
        str(status.service or "").strip().lower(): status
        for status in result.scalars()
    }


async def _latest_jobs_by_integration(
    db: AsyncSession, organization_id: int, integrations
) -> dict[int, SyncJob]:
    integration_ids = [integration.id for integration in integrations]
    if not integration_ids:
        return {}

    ranked_jobs = (
        select(
            SyncJob.id.label("id"),
            SyncJob.integration_id.label("integration_id"),
            func.row_number()
            .over(
                partition_by=SyncJob.integration_id,
                order_by=(
                    SyncJob.finished_at.desc().nulls_last(),
                    SyncJob.queued_at.desc(),
                    SyncJob.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(
            SyncJob.organization_id == organization_id,
            SyncJob.integration_id.in_(integration_ids),
        )
        .subquery()
    )
    result = await db.execute(
        select(SyncJob)
        .join(ranked_jobs, SyncJob.id == ranked_jobs.c.id)
        .where(ranked_jobs.c.rank == 1)
    )
    return {
        int(job.integration_id): job
        for job in result.scalars()
        if job.integration_id
    }


def _to_response(
    integration, service_status=None, latest_job=None
) -> IntegrationResponse:
    last_job_error = getattr(latest_job, "error_message", None) if latest_job else None
    service_error = (
        getattr(service_status, "last_error", None)
        if service_status is not None
        else None
    )
    return IntegrationResponse(
        id=integration.id,
        provider=integration.provider,
        name=integration.name,
        kind=integration.kind,
        status=integration.status,
        exchange_code=integration.exchange_code,
        account_ref=integration.account_ref,
        wallet_address=integration.wallet_address,
        chain=integration.chain,
        is_active=integration.is_active,
        last_synced_at=integration.last_synced_at,
        is_healthy=(
            getattr(service_status, "is_healthy", None)
            if service_status is not None
            else None
        ),
        health_status=_health_status(integration, service_status, latest_job),
        last_error=last_job_error or service_error,
        last_check=(
            getattr(service_status, "last_check", None)
            if service_status is not None
            else None
        ),
        last_job_status=getattr(latest_job, "status", None) if latest_job else None,
        last_job_error=last_job_error,
        last_job_finished_at=(
            getattr(latest_job, "finished_at", None) if latest_job else None
        ),
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
    service_statuses = await _service_statuses_by_key(
        db, identity.organization.id, integrations
    )
    latest_jobs = await _latest_jobs_by_integration(
        db, identity.organization.id, integrations
    )
    return [
        _to_response(
            integration,
            service_statuses.get(_service_key_for_integration(integration)),
            latest_jobs.get(integration.id),
        )
        for integration in integrations
    ]


@router.post("/verify", response_model=IntegrationVerifyResponse)
async def verify_integration_credentials(
    payload: IntegrationVerifyRequest,
    identity: IdentityContext = Depends(require_role("member")),
):
    exchange_code = payload.exchange_code.strip().lower()
    if exchange_code in CRYPTOBOT_PROVIDER_CODES or exchange_code == "cryptobot":
        try:
            await crypto_bot_app_client.verify_token(str(payload.api_token or ""))
            return IntegrationVerifyResponse(ok=True, error=None)
        except Exception as exc:
            return IntegrationVerifyResponse(ok=False, error=str(exc))

    ok, error = await ccxt_manager.verify_credentials(
        exchange_id=exchange_code,
        api_key=str(payload.api_key or ""),
        api_secret=str(payload.api_secret or ""),
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
    validate_integration_create_payload(payload)
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
        api_token=payload.api_token,
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
            "has_api_token": bool(str(payload.api_token or "").strip()),
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
