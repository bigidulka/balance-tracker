# REFORM Report

## 1) Executive summary

This report consolidates discovery and implementation evidence into a **minimal-risk refactor blueprint** for the current Balance Tracker stack (FastAPI monolith + Telegram bot).

Recommended strategy: **Option B — contract-first cleanup** (small, reversible slices, no big-bang rewrite).

Why:
- Current architecture already has strong foundations (tenant-aware APIs, entitlement enforcement, sync job queue, load-test harness).
- Main risk is not missing capability; it is **coupling + duplicated refresh/control logic** and **hardcoded behavior drift**.
- Incremental boundary extraction and contract tightening provides most of the benefit with lowest blast radius.

---

## 2) Current architecture (evidence baseline)

### 2.1 Runtime context
- API app with startup/lifespan hooks, in-process background refresh loop, and in-process sync workers: `app/main.py:140`
- Router composition in monolith: `app/main.py:220`
- Bot is the primary UI surface and integrates through API client methods: `bot/api_client.py:8`

### 2.2 Critical business paths
- Entitlements policy resolution and refresh gating: `app/services/entitlements_service.py:196`, `app/services/entitlements_service.py:367`
- Sync job dedupe + queue claim/run semantics: `app/services/sync_job_service.py:45`, `app/services/sync_job_service.py:223`
- Balance refresh semantics (`success/partial/failed`) recorded via SyncJob: `app/services/balance_service.py:269`
- Dashboard aggregate endpoint for bot main screen: `app/routers/balances.py:191`
- Integration lifecycle endpoints (activate/deactivate/refresh queue): `app/routers/integrations.py:99`, `app/routers/integrations.py:132`, `app/routers/integrations.py:165`
- Bot transaction filters + integration controls: `bot/handlers/router.py:595`, `bot/handlers/router.py:696`

### 2.3 Existing proof baseline
- Lifecycle/entitlements/refresh semantics tests: `tests/test_entitlement_checks.py:420`
- Cache behavior tests: `tests/test_balance_cache_behavior.py`
- Transaction singleflight cancellation tests: `tests/test_transaction_singleflight_cancellation.py`
- Load profiles and thresholds: `loadtests/common.js:37`, `loadtests/steady.js:8`

---

## 3) Target architecture (incremental, no rewrite)

### 3.1 What stays unchanged
- FastAPI monolith deployment shape.
- SQLAlchemy async session model.
- SyncJob as durable refresh execution primitive.
- Telegram bot as UI channel.

### 3.2 What changes (gradually)
1. **Refresh orchestration boundary**
   - Consolidate refresh gating/queue semantics behind one application service.
2. **Contract boundary**
   - Freeze API response shapes used by bot (dashboard/entitlements/integrations/transactions).
3. **Config boundary**
   - Remove distributed magic constants into typed config (without changing behavior).
4. **Operational boundary**
   - Isolate in-process background loops behind explicit mode selection (worker profile vs web profile).

---

## 4) ADR candidates (3–7)

### ADR-001: Keep monolith, extract service boundaries first
**Decision**: No microservice split now. Extract orchestration seams inside monolith.

**Rationale**: Highest reversibility, lowest migration risk.

**Impacted areas**: `app/main.py:140`, `app/services/*`.

---

### ADR-002: Make SyncJob the canonical refresh execution path
**Decision**: Align manual/background refresh flows to one execution contract and status model.

**Rationale**: Reduces semantics drift between `BalanceService.refresh_all` and `SyncJobService.run_claimed_job`.

**Evidence anchors**: `app/services/balance_service.py:269`, `app/services/sync_job_service.py:235`.

---

### ADR-003: Contract-first API for bot UI
**Decision**: Treat `dashboard/summary`, entitlements, integrations, and transactions payloads as versioned contracts.

**Rationale**: Bot UX stability depends on predictable shape and field semantics.

**Evidence anchors**: `app/routers/balances.py:191`, `bot/handlers/router.py:136`.

---

### ADR-004: Typed centralized configuration, behavior-preserving defaults
**Decision**: Keep defaults, but centralize ownership and remove scattered hardcoded constants.

**Rationale**: Limits hidden behavior changes and environment drift.

**Evidence anchors**: `app/core/config.py:15`, `bot/config.py:36`.

---

### ADR-005: Explicit runtime profile for background loops
**Decision**: Gate refresh loop and in-process workers via clear profile flags and startup assertions.

**Rationale**: Prevent accidental dual execution and improve operational clarity.

**Evidence anchors**: `app/main.py:44`, `app/main.py:50`, `app/main.py:160`, `app/main.py:166`.

---

## 5) Hardcoded behavior inventory and extraction plan

| Category | Evidence | Risk | Low-risk extraction |
|---|---|---|---|
| Default org constant | `app/models/balance.py:18` | Tenant leakage assumptions | Move to config-backed default; keep existing default value initially |
| DB/env defaults incl. credentials | `app/core/config.py:19` | Env drift/security misconfig | Keep values but mark required in prod profile checks |
| Startup delay / polling constants | `app/main.py:62`, `app/main.py:115` | Hidden runtime behavior | Promote to settings keys with same defaults |
| API client timeouts | `bot/api_client.py:30`, `bot/api_client.py:74` | Inconsistent UX on slow backends | Config-key these values; preserve defaults |
| Runtime user filter defaults | `bot/services/runtime.py:16` | In-memory behavior mismatch across restarts | Keep ephemeral for now; document and later persist if needed |
| UI cooldown text logic | `bot/keyboards/inline.py:31`, `bot/messages.py:61` | Divergent messaging semantics | Keep one source for refresh-state formatting |
| Load test defaults | `loadtests/common.js:38` | Benchmark comparability drift | Version profile defaults + explicit env matrix |

---

## 6) Incremental refactor batches (small, reversible)

### Batch 0 — Safety net freeze
- Goal: Baseline protection before structural edits.
- Actions:
  - Ensure characterization tests cover refresh semantics + bot contracts.
- Checkpoint:
  - `python -m unittest tests.test_entitlement_checks tests.test_balance_cache_behavior tests.test_transaction_singleflight_cancellation`
- Rollback: No behavior changes yet.

### Batch 1 — Refresh orchestration seam
- Goal: Create a thin orchestration layer for refresh entry points.
- Files: `app/services/balance_service.py`, `app/services/sync_job_service.py`, `app/services/entitlements_service.py`.
- Tests: extend `tests/test_entitlement_checks.py` around mixed refresh paths.
- Rollback: keep old method calls behind adapter.

### Batch 2 — Contract locking for bot payloads
- Goal: Freeze response contracts and validate keys/semantics.
- Files: `app/routers/balances.py`, `app/routers/integrations.py`, bot parser/render paths.
- Tests: add API contract tests (shape + required keys).
- Rollback: maintain fallback path in bot main renderer.

### Batch 3 — Config de-hardcoding (behavior preserving)
- Goal: Replace scattered constants with settings references.
- Files: `app/main.py`, `bot/api_client.py`, `bot/services/runtime.py`, `loadtests/common.js`.
- Tests: smoke + config parsing tests.
- Rollback: defaults unchanged.

### Batch 4 — Runtime profile clarity
- Goal: Explicitly separate web/API-only vs worker-enabled startup profiles.
- Files: `app/main.py`, config docs.
- Tests: startup mode tests, metrics visibility checks.
- Rollback: revert profile switch, keep flags as currently interpreted.

### Batch 5 — UX consistency polish
- Goal: Ensure one refresh-state semantic across message text + buttons + callbacks.
- Files: `bot/messages.py`, `bot/keyboards/inline.py`, `bot/handlers/router.py`.
- Tests: bot handler unit/functional checks.
- Rollback: fallback to existing callbacks and noop behavior.

---

## 7) Tradeoff matrix and prioritized roadmap

### 7.1 Options

| Option | Benefit | Cost | Risk | Reversibility | Blast radius |
|---|---|---|---|---|---|
| A. Big-bang architectural split | Potentially clean long-term boundaries | Very high | Very high | Low | High |
| B. Contract-first cleanup (recommended) | High practical reliability/modifiability gain | Medium | Low–Medium | High | Low–Medium |
| C. Minimal patching only | Low immediate effort | Low | Medium (debt persists) | High | Low |

### 7.2 Priority order
1. Batch 0 (safety net)
2. Batch 1 (refresh seam)
3. Batch 2 (contract lock)
4. Batch 3 (config de-hardcoding)
5. Batch 4 (runtime profile clarity)
6. Batch 5 (UX consistency)

---

## 8) Quality scenarios and DoD rubric

### Performance
- Scenario: steady read path under load keeps p95 and error thresholds.
- Evidence: `loadtests/steady.js:26`, CI/loadtest reports.

### Reliability
- Scenario: concurrent duplicate refresh enqueue yields one active job.
- Evidence: `tests/test_entitlement_checks.py:326`.

### Security / tenant isolation
- Scenario: cross-tenant integration access returns not found.
- Evidence: `tests/test_entitlement_checks.py:469`.

### Modifiability
- Scenario: adding new refresh rule changes one orchestration surface, not N call sites.
- Evidence: post-batch dependency map + changed-files count.

### Deployability / operations
- Scenario: startup profile unambiguously states worker/refresh loop mode.
- Evidence: startup logs/metrics from `app/main.py:160` and `app/main.py:166`.

### Observability
- Scenario: refresh and queue outcomes are measurable (completed/failed/deduped/rate-limited).
- Evidence: metrics increments in `app/services/sync_job_service.py:73`, `app/main.py:128`.

---

## 9) Proof pack (current run)

### Executed in this session
- `python -m unittest tests.test_entitlement_checks tests.test_balance_cache_behavior tests.test_transaction_singleflight_cancellation`
- Result: **18 tests, OK**.

### Existing evidence anchors
- Integration lifecycle and tenant checks: `tests/test_entitlement_checks.py:420`
- Refresh partial/failure semantics: `tests/test_entitlement_checks.py:226`
- SyncJob atomic dedupe: `tests/test_entitlement_checks.py:326`

### Evidence gaps to close in next slice
- API contract tests for `/api/v1/dashboard/summary` and integration lifecycle router responses.
- Bot callback-level tests for transaction filter toggles and integration actions.

---

## 10) Final recommendation

Proceed with **Option B** using batches above. Do not rewrite architecture wholesale. Treat contracts, refresh orchestration, and config centralization as first-class refactor axes, each with characterization tests and rollback-friendly slices.
