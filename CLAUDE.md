# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

### Environment and dependencies
- Create venv + install API deps:
  - `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Bot-only deps (if running bot separately):
  - `pip install -r bot/requirements.txt`
- Copy env template:
  - `cp .env.example .env`

### Run services locally (from repo root)
- API:
  - `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
- Worker (sync job processor):
  - `python -m app.worker`
- Telegram bot:
  - `python -m bot.main`

### Docker compose stack
- Start full stack (postgres + api + worker + bot):
  - `docker compose up --build`
- Stop stack:
  - `docker compose down`

### Database migrations
- Apply migrations:
  - `alembic upgrade head`
- Create migration:
  - `alembic revision --autogenerate -m "<message>"`

### Tests
- Run core regression suite used in refactor docs:
  - `python -m unittest tests.test_entitlement_checks tests.test_balance_cache_behavior tests.test_transaction_singleflight_cancellation`
- Run one module:
  - `python -m unittest tests.test_api_contract_locks`
- Run one specific test:
  - `python -m unittest tests.test_entitlement_checks.EntitlementEnforcementTests.test_free_plan_denies_dex_integration_creation`

### Load testing (k6)
- `k6 run loadtests/steady.js`
- `k6 run loadtests/burst.js`
- `k6 run loadtests/degraded.js`
- Docker helper script:
  - `loadtests/run_k6_docker.sh`

## High-level architecture

### Runtime shape
- `app/main.py` hosts the FastAPI app and lifecycle bootstrapping.
  - Initializes DB (`init_db`), ensures default plans/subscriptions.
  - Optionally starts two in-process loops (feature-flag driven):
    - background refresh loop
    - in-process sync workers
- `app/worker.py` is a dedicated worker runtime that continuously claims and executes queued sync jobs.
- `bot/main.py` runs an Aiogram Telegram bot; bot UI calls API endpoints via `bot/api_client.py`.

### Core domains and boundaries
- **Auth + tenancy + RBAC**
  - Token parsing and organization resolution live in `app/core/dependencies.py` and `app/services/auth_service.py`.
  - API requests are tenant-scoped via bearer token + org override (`X-Organization-Id` / query `organization_id`).
  - Role gates are enforced with `require_role(...)` dependencies.
- **Integrations + entitlements**
  - Integration lifecycle is handled by `app/services/integration_service.py` and `app/routers/integrations.py`.
  - Plan policy enforcement (limits, throttling, DEX capability) is centralized in `app/services/entitlements_service.py`.
- **Refresh orchestration + job queue**
  - `app/services/refresh_orchestrator.py` is the orchestration entrypoint.
  - `app/services/sync_job_service.py` manages enqueue/claim/run lifecycle for `sync_jobs` with optional dedupe.
  - Provider execution is abstracted behind registry/strategy in `app/services/integrations/registry.py`.
- **Balances/transactions read model**
  - `app/services/balance_service.py` and `app/services/transaction_service.py` handle fetch/refresh paths.
  - Dashboards, balances, history, refresh endpoints are in `app/routers/balances.py` and `app/routers/transactions.py`.

### Persistence model
- Main schema is in `app/models/balance.py`.
- Key entities:
  - tenancy/auth: `Organization`, `User`, `OrganizationMembership`
  - integration pipeline: `Integration`, `IntegrationSecret`, `SyncJob`
  - portfolio data: `Balance`, `BalanceHistory`, `ServiceStatus`, `Transaction`
  - billing/planing: `Plan`, `Subscription`, `UsageEvent`, `BillingEvent`, `BillingWebhookEvent`, `AuditLog`
- DB backend selection is config-driven in `app/core/config.py` + `app/core/database.py`:
  - SQLite path for local/dev fallback
  - PostgreSQL as primary runtime path
- Alembic is configured in `alembic/env.py` to use app settings for DSN.

### API surface
- Routers mounted in `app/main.py`:
  - `/api/v1/auth`
  - `/api/v1` (balances, refresh, dashboard, health)
  - `/api/v1/transactions`
  - `/api/v1/integrations`
  - `/api/v1/billing`
  - observability endpoints (`/metrics`, `/health`, `/readiness`)

### Configuration model
- API config: `app/core/config.py` (`pydantic-settings`, `.env` driven).
- Bot config: `bot/config.py` (dataclass from environment).
- Important runtime toggles controlling behavior split:
  - `ENABLE_INPROCESS_REFRESH_LOOP`
  - `ENABLE_WORKER`
  - legacy aliases: `ENABLE_LEGACY_BACKGROUND_REFRESH_LOOP`, `ENABLE_INPROCESS_SYNC_WORKER`

### Project context from decision logs
- `DECISIONS.md` is the canonical decisions log for plan policy shape, entitlement enforcement points, and load-test profiles.
- `REFORM_REPORT.md` documents current refactor strategy and evidence anchors; useful when changing orchestration/contracts without broad rewrites.
