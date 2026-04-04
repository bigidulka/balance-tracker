# Scaling Required Changes

## Mandatory

1. Move runtime DB from SQLite to PostgreSQL.
2. Move bot FSM and per-user runtime state out of process memory into shared storage.
3. Move notification subscriber/state tracking out of process memory.
4. Keep user-facing screens on cached snapshots instead of live exchange fan-out.
5. Run refresh/sync work in dedicated workers, not inside the API request path.
6. Add shared cache and request deduplication for hot API reads.
7. Replace stale DEX fallback with a live wallet source.
8. Separate exchange operational failures from code failures in observability.

## Implemented In This Step

1. Redis added to Docker compose for bot runtime storage.
2. Aiogram FSM will use Redis when available.
3. Bot user settings and subscriber runtime moved to Redis-backed storage with fallback.
4. Notification state/history was moved off process memory into Redis-backed shared storage with fallback.
5. API runtime was switched from SQLite override to PostgreSQL config in `.env`.
6. `okx_wallet` service enablement was decoupled from `okx` CEX disable logic.

## Still Pending

1. Add shared cache for `entitlements`, `dashboard/summary`, and `balances/cached`.
2. Move background refresh fully to worker-only execution.
3. Replace live DEX fetch path with a stable source that does not rely on stale fallback.
