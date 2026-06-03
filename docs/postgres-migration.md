# PostgreSQL Runtime Migration

This project should run API, worker, and scheduler on PostgreSQL for 100+ concurrent users.
SQLite remains only as the migration source and local fallback.

`DATABASE_USE_SQLITE` may still exist in legacy `.env` files. Compose uses
`APP_DATABASE_USE_SQLITE` for the runtime switch so old `.env` values do not
accidentally keep services on SQLite.

## Safe Production Sequence

1. Stop write workers before copying:
   - `docker compose stop api worker scheduler bot`
2. Start PostgreSQL only:
   - `docker compose up -d postgres`
3. Build the app image without starting API:
   - `docker compose build api worker scheduler`
4. Copy SQLite data into PostgreSQL:
   - `docker compose run --rm api python scripts/migrate_sqlite_to_postgres.py --truncate`
5. Start runtime services on PostgreSQL:
   - `docker compose up -d api worker scheduler bot`
6. Verify:
   - `docker compose ps`
   - `curl -fsS http://127.0.0.1:8001/readiness`
   - Compare key counts in SQLite and PostgreSQL.

## Notes

- The migration script stamps Alembic head after creating the schema.
- Re-running against non-empty PostgreSQL requires `--truncate`.
- Keep the SQLite file until PostgreSQL runtime has been verified and backed up.
