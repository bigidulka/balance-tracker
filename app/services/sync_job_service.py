import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.metrics_service import metrics_service

from fastapi import HTTPException
from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.balance import Integration, IntegrationSecret, SyncJob
from app.repositories.balance import BalanceRepository, ServiceStatusRepository
from app.schemas.balance import AccountBalanceSchema, AssetSchema, ServiceBalanceSchema
from app.services.entitlements_service import EntitlementsService
from app.services.integrations import get_provider

settings = get_settings()


class SyncJobService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.entitlements = EntitlementsService(session)
        self.balance_repo = BalanceRepository(session)
        self.status_repo = ServiceStatusRepository(session)

    async def recover_stale_running_jobs(
        self,
        *,
        timeout_seconds: int | None = None,
        organization_id: int | None = None,
    ) -> int:
        timeout = max(60, int(timeout_seconds or settings.sync_job_running_timeout_seconds))
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout)
        predicates = [
            SyncJob.status == "running",
            SyncJob.started_at.is_not(None),
            SyncJob.started_at < cutoff,
        ]
        if organization_id is not None:
            predicates.append(SyncJob.organization_id == organization_id)

        result = await self.session.execute(
            update(SyncJob)
            .where(*predicates)
            .values(
                status="failed",
                finished_at=datetime.now(timezone.utc),
                error_message="Recovered stale running job after worker restart",
                result={"status": "failed", "code": "stale_running_job_recovered"},
            )
        )
        await self.session.commit()
        recovered = int(result.rowcount or 0)
        if recovered:
            metrics_service.inc("sync_jobs_stale_recovered_total", recovered)
        return recovered

    async def _persist_balance_from_refresh_result(
        self,
        organization_id: int,
        refresh_data: dict[str, Any] | None,
    ) -> None:
        if not isinstance(refresh_data, dict):
            return
        raw_balance = refresh_data.get("balance")
        if not isinstance(raw_balance, dict):
            return

        balance = ServiceBalanceSchema(
            integration_id=(
                int(raw_balance.get("integration_id"))
                if raw_balance.get("integration_id") is not None
                else None
            ),
            service=str(raw_balance.get("service") or ""),
            accounts=[
                AccountBalanceSchema(
                    account_type=str(account.get("account_type") or "spot"),
                    assets=[
                        AssetSchema(
                            coin=str(asset.get("coin") or ""),
                            amount=float(asset.get("amount") or 0.0),
                            value_usd=float(asset.get("value_usd") or 0.0),
                        )
                        for asset in (account.get("assets") or [])
                        if isinstance(asset, dict)
                    ],
                    total_usd=float(account.get("total_usd") or 0.0),
                )
                for account in (raw_balance.get("accounts") or [])
                if isinstance(account, dict)
            ],
            assets=[
                AssetSchema(
                    coin=str(asset.get("coin") or ""),
                    amount=float(asset.get("amount") or 0.0),
                    value_usd=float(asset.get("value_usd") or 0.0),
                )
                for asset in (raw_balance.get("assets") or [])
                if isinstance(asset, dict)
            ],
            total_usd=float(raw_balance.get("total_usd") or 0.0),
            updated_at=datetime.now(timezone.utc),
            actual=bool(raw_balance.get("actual", True)),
        )

        if not balance.service:
            return

        if not balance.actual:
            await self.status_repo.update_status(
                balance.service,
                is_healthy=False,
                last_error="Degraded balance payload",
                organization_id=organization_id,
            )
            raise ValueError(f"Degraded balance payload for {balance.service}")

        await self.balance_repo.save_balance(
            service=balance.service,
            assets=balance.assets,
            total_usd=balance.total_usd,
            actual=balance.actual,
            accounts=balance.accounts,
            organization_id=organization_id,
            integration_id=balance.integration_id,
        )
        await self.status_repo.update_status(
            balance.service,
            is_healthy=True,
            organization_id=organization_id,
        )

    def _attach_retry_metadata(self, detail: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": "refresh_rate_limited",
            "message": "Refresh rate limit exceeded for current plan",
        }
        if isinstance(detail, dict):
            payload.update(detail)
        return payload

    def _payload_fingerprint(self, payload: dict[str, Any] | None) -> str:
        normalized_payload = payload or {}
        serialized = json.dumps(
            normalized_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialized.encode()).hexdigest()

    def _build_dedupe_key(
        self,
        organization_id: int,
        integration_id: int | None,
        job_type: str,
        payload: dict[str, Any] | None,
    ) -> str:
        payload_fingerprint = self._payload_fingerprint(payload)
        integration_marker = "null" if integration_id is None else str(integration_id)
        return f"{organization_id}:{integration_marker}:{job_type}:{payload_fingerprint}"

    async def _find_active_duplicate_job(self, dedupe_key: str) -> SyncJob | None:
        active_statuses = ["queued", "running"]
        query = (
            select(SyncJob)
            .where(SyncJob.dedupe_key == dedupe_key, SyncJob.status.in_(active_statuses))
            .order_by(SyncJob.queued_at.desc(), SyncJob.id.desc())
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def enqueue_job(
        self,
        organization_id: int,
        integration_id: int | None,
        job_type: str = "refresh",
        payload: dict[str, Any] | None = None,
    ) -> SyncJob:
        metrics_service.inc("sync_jobs_enqueued_total")

        dedupe_key = None
        if settings.enable_syncjob_dedupe:
            dedupe_key = self._build_dedupe_key(
                organization_id=organization_id,
                integration_id=integration_id,
                job_type=job_type,
                payload=payload,
            )
            duplicate = await self._find_active_duplicate_job(dedupe_key=dedupe_key)
            if duplicate is not None:
                metrics_service.inc("sync_jobs_deduped_total")
                return duplicate

        job = SyncJob(
            organization_id=organization_id,
            integration_id=integration_id,
            job_type=job_type,
            status="queued",
            dedupe_key=dedupe_key,
            payload=payload or {},
            result={},
        )
        self.session.add(job)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            if not settings.enable_syncjob_dedupe or dedupe_key is None:
                raise
            duplicate = await self._find_active_duplicate_job(dedupe_key=dedupe_key)
            if duplicate is None:
                raise
            metrics_service.inc("sync_jobs_deduped_total")
            return duplicate

        await self.session.refresh(job)
        return job

    async def queue_refresh_for_organization(
        self,
        organization_id: int,
        payload: dict[str, Any] | None = None,
    ) -> list[SyncJob]:
        await self.entitlements.ensure_refresh_interval_for_organization(organization_id)

        query: Select[tuple[Integration]] = select(Integration).where(
            Integration.organization_id == organization_id,
            Integration.is_active == True,
        )
        result = await self.session.execute(query)
        integrations = list(result.scalars().all())

        jobs: list[SyncJob] = []
        for integration in integrations:
            job = await self.enqueue_job(
                organization_id=organization_id,
                integration_id=integration.id,
                job_type="refresh",
                payload=payload,
            )
            jobs.append(job)

        return jobs

    async def queue_refresh_for_integration(
        self,
        organization_id: int,
        integration_id: int,
        payload: dict[str, Any] | None = None,
    ) -> SyncJob:
        await self.entitlements.ensure_refresh_interval_for_organization(organization_id)

        return await self.enqueue_job(
            organization_id=organization_id,
            integration_id=integration_id,
            job_type="refresh",
            payload=payload,
        )

    async def _claim_next_job_postgres(
        self, organization_id: int | None = None
    ) -> SyncJob | None:
        claim_stmt = (
            select(SyncJob.id)
            .where(SyncJob.status == "queued")
            .order_by(SyncJob.queued_at.asc(), SyncJob.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if organization_id is not None:
            claim_stmt = claim_stmt.where(SyncJob.organization_id == organization_id)

        claim_result = await self.session.execute(claim_stmt)
        claimed_job_id = claim_result.scalar_one_or_none()
        if claimed_job_id is None:
            await self.session.rollback()
            return None

        now = datetime.now(timezone.utc)
        update_result = await self.session.execute(
            update(SyncJob)
            .where(SyncJob.id == claimed_job_id)
            .values(status="running", started_at=now)
            .returning(SyncJob.id)
        )
        updated_job_id = update_result.scalar_one_or_none()
        if updated_job_id is None:
            await self.session.rollback()
            return None

        await self.session.commit()
        return await self.session.get(SyncJob, updated_job_id)

    async def _claim_next_job_fallback(
        self, organization_id: int | None = None
    ) -> SyncJob | None:
        claim_stmt = (
            select(SyncJob.id)
            .where(SyncJob.status == "queued")
            .order_by(SyncJob.queued_at.asc(), SyncJob.id.asc())
            .limit(1)
        )
        if organization_id is not None:
            claim_stmt = claim_stmt.where(SyncJob.organization_id == organization_id)

        claim_result = await self.session.execute(claim_stmt)
        candidate_job_id = claim_result.scalar_one_or_none()
        if candidate_job_id is None:
            await self.session.rollback()
            return None

        now = datetime.now(timezone.utc)
        update_stmt = (
            update(SyncJob)
            .where(SyncJob.id == candidate_job_id, SyncJob.status == "queued")
            .values(status="running", started_at=now)
            .returning(SyncJob.id)
        )

        update_result = await self.session.execute(update_stmt)
        claimed_job_id = update_result.scalar_one_or_none()
        if claimed_job_id is None:
            await self.session.rollback()
            return None

        await self.session.commit()
        return await self.session.get(SyncJob, claimed_job_id)

    async def claim_next_job(self, organization_id: int | None = None) -> SyncJob | None:
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect == "postgresql":
            return await self._claim_next_job_postgres(organization_id=organization_id)
        return await self._claim_next_job_fallback(organization_id=organization_id)

    async def run_next_job(self, organization_id: int | None = None) -> SyncJob | None:
        job = await self.claim_next_job(organization_id=organization_id)
        if job is None:
            return None
        return await self.run_claimed_job(job)

    async def run_claimed_job(self, job: SyncJob) -> SyncJob:
        try:
            payload = dict(job.payload or {})
            if payload.get("enforce_refresh_interval_at_run"):
                await self.entitlements.ensure_refresh_interval_for_organization(
                    job.organization_id
                )

            integration_result = await self.session.execute(
                select(Integration).where(Integration.id == job.integration_id)
            )
            integration = integration_result.scalar_one_or_none()

            if integration is None:
                raise ValueError(f"Integration {job.integration_id} not found")

            provider = get_provider(integration.provider)

            # Inject integration secrets into payload so providers
            # like cryptobot can access api_token from payload["api_token"]
            secrets_rows = await self.session.execute(
                select(IntegrationSecret).where(
                    IntegrationSecret.integration_id == integration.id
                )
            )
            secrets_list = secrets_rows.scalars().all()
            for secret in secrets_list:
                if secret.key_name and secret.secret_value:
                    payload[secret.key_name] = secret.secret_value

            refresh_result = await provider.refresh(
                organization_id=job.organization_id,
                integration=integration,
                payload=payload,
            )

            await self._persist_balance_from_refresh_result(
                organization_id=job.organization_id,
                refresh_data=refresh_result.data,
            )

            integration.last_synced_at = datetime.now(timezone.utc)
            job.result = {
                "provider": integration.provider,
                "status": refresh_result.status,
                "message": refresh_result.message,
                "data": refresh_result.data,
            }
            job.status = (
                "completed"
                if refresh_result.status in {"ok", "not_implemented"}
                else "failed"
            )
            job.error_message = (
                refresh_result.message if job.status == "failed" else None
            )
        except HTTPException as exc:
            job.status = "failed"
            detail = self._attach_retry_metadata(exc.detail)
            job.error_message = str(detail.get("message") or "Refresh rate limited")
            job.result = {
                "status": "rate_limited",
                "code": detail.get("code", "refresh_rate_limited"),
                "message": detail.get("message", "Refresh rate limit exceeded"),
                "retry_after_seconds": detail.get("retry_after_seconds"),
                "min_refresh_interval_seconds": detail.get("min_refresh_interval_seconds"),
            }
        except Exception as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.result = {}
        finally:
            job.finished_at = datetime.now(timezone.utc)
            await self.session.commit()
            await self.session.refresh(job)

        return job

    async def run_job(self, job_id: int) -> SyncJob:
        job_result = await self.session.execute(select(SyncJob).where(SyncJob.id == job_id))
        job = job_result.scalar_one()

        if job.status != "queued":
            return job

        now = datetime.now(timezone.utc)
        claim_result = await self.session.execute(
            update(SyncJob)
            .where(SyncJob.id == job_id, SyncJob.status == "queued")
            .values(status="running", started_at=now)
            .returning(SyncJob.id)
        )
        claimed_job_id = claim_result.scalar_one_or_none()
        if claimed_job_id is None:
            await self.session.rollback()
            refreshed = await self.session.execute(
                select(SyncJob).where(SyncJob.id == job_id)
            )
            return refreshed.scalar_one()

        await self.session.commit()
        claimed_job = await self.session.get(SyncJob, claimed_job_id)
        return await self.run_claimed_job(claimed_job)
