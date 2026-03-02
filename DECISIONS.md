# Decisions and Discussion Log

This file captures stable implementation decisions so product and engineering context is not lost between sessions.

## Policy model (free/full)

- Policy source of truth is `plans.policy_json` with structure:
  - `limits.max_integrations`
  - `limits.max_accounts_per_exchange`
  - `throttling.min_refresh_interval_seconds`
  - `capabilities.allow_dex` (`0`/`1`)
- Resolution order:
  1. `EntitlementsService.DEFAULT_POLICY`
  2. Legacy scalar columns on `Plan` (`max_integrations`, `min_refresh_interval_seconds`)
  3. `policy_json` overrides (highest priority)
- Effective defaults for free fallback:
  - `max_integrations=1000`
  - `max_accounts_per_exchange=1`
  - `min_refresh_interval_seconds=3600`
  - `allow_dex=0`
- Effective defaults for full:
  - `max_integrations=1000`
  - `max_accounts_per_exchange=0` (treated as unlimited)
  - `min_refresh_interval_seconds=300`
  - `allow_dex=1`

## Account model decisions

- Integration identity is explicitly typed by `kind`:
  - `cex`: requires `exchange_code` + `account_ref`
  - `dex`: requires `wallet_address` + `chain`
- Uniqueness per organization is enforced by composite unique constraints and service-level duplicate checks.
- `exchange_code` and `chain` are normalized to lowercase at service layer.

## Bot migration decisions

- Bot migration target is `_bot_template` scaffold while preserving current product behavior.
- API auth and tenant headers remain required for bot API calls (`Authorization`, `X-Organization-Id`/`X-Org-Id`) during migration.
- DEX/CEEX visualization in bot UI is retained, but backend entitlement policies are authoritative.

## Enforcement points

- **Integration creation policy**
  - `app/services/entitlements_service.py` (`ensure_can_create_integration`)
  - Enforces plan integration cap, per-exchange CEX cap, and DEX capability gate.
- **Refresh throttling policy**
  - `app/services/entitlements_service.py` (`ensure_refresh_interval*`)
  - Applied in all refresh entry points:
    - `app/services/sync_job_service.py`
    - `app/services/balance_service.py`
    - `app/services/transaction_service.py`
    - background loop in `app/main.py`
- **Retry metadata contract**
  - Rate-limit failures surface `retry_after_seconds` and `min_refresh_interval_seconds` in SyncJob result payload.

## P3 load testing profile

- k6 scenarios are located under `loadtests/`:
  - `steady.js` — constant arrival rate baseline for ~1000-account read-heavy simulation
  - `burst.js` — ramped burst traffic to test short peak handling
  - `degraded.js` — lower-rate long-run profile for stressed/degraded environments
- Shared environment/config logic is in `loadtests/common.js`.
  - Core env knobs: `BASE_URL`, `AUTH_TOKEN`, `ORGANIZATION_ID`, `ORGANIZATION_COUNT`, `ORGANIZATION_IDS`, `ACCOUNTS_PER_ORG`
  - Optional behavior knobs: `THINK_TIME_MS`, `REQUEST_TIMEOUT_MS`, `FORCE_REFRESH_RATIO`, `ENABLE_MUTATING_ENDPOINTS`
- Thresholds are profile-specific and overrideable via env vars (for example `THRESHOLD_HTTP_REQ_P95_STEADY`, `THRESHOLD_HTTP_REQ_FAILED_BURST`).
- `loadtests/run_k6_docker.sh` forwards all threshold override env vars to containerized k6 runs.

### k6 run examples

- Steady run:
  - `k6 run loadtests/steady.js`
- Burst run:
  - `k6 run loadtests/burst.js`
- Degraded run:
  - `k6 run loadtests/degraded.js`
- Example with auth + multi-org simulation:
  - `BASE_URL=http://localhost:8000 AUTH_TOKEN=<token> ORGANIZATION_ID=1 ORGANIZATION_COUNT=10 ACCOUNTS_PER_ORG=100 k6 run loadtests/steady.js`

## Secrets rotation runbook (P0.1)

- Scope of emergency/regular rotation:
  - exchange API credentials (`*_API_KEY`, `*_SECRET`, exchange passwords where applicable);
  - bot token (`BOT_TOKEN`);
  - auth/integration secrets (`JWT_SECRET_KEY`, `INTEGRATION_SECRET_KEY`);
  - DB credentials (`POSTGRES_PASSWORD` / DSN secret fragment);
  - proxy credentials (`PROXY_USERNAME`, `PROXY_PASSWORD`).
- Rotation sequence (safe order):
  1. Generate new secrets in provider consoles/vault.
  2. Update runtime secret store / deployment environment.
  3. Roll API and worker processes.
  4. Roll bot process.
  5. Revoke old credentials in provider consoles.
  6. Validate `/health` and `/readiness`, then perform one manual refresh.
- Incident rule: if `.env` or DB artifact leaked into history/image, rotate **all** affected keys immediately (do not rely on later cleanup only).
- Repository rule: keep only templates (`.env.example`, `_bot_template/.env.example`) in VCS; never commit real secrets.

## Open questions

- Should free-plan DEX denial be hardcoded via policy (`allow_dex=0`) only, or also constrained by provider allow-list?
- Do we want separate CEX limits per plan tier by exchange family (spot-only vs futures-enabled) or keep one shared cap?
- Should refresh throttling be per-organization global (current) or per integration/provider?
- Should `allow_dex` evolve from numeric (`0`/`1`) to boolean schema in policy JSON?
- Are there migration scripts needed to backfill `capabilities.allow_dex` for existing plan rows?
