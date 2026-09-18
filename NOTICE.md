# Notice

## Implementation code

The application code (`app/`, `bot/`, `alembic/`, `tests/`, `loadtests/`, `scripts/`,
Docker and Compose files) and the bundled `debank-sdk/` service are the author's own work.
Both are MIT licensed: see [LICENSE](LICENSE) and `debank-sdk/package.json`.

## Research scripts

`scripts/research/debank_signer/` documents reverse-engineering work against the public
DeBank web application: it captures the browser signing routine to sign requests headlessly.
DeBank is a third-party service with its own terms of use; these scripts are research tooling
for personal use, are not part of the runtime, and are not exercised by the test suite.
`scripts/probes/` contains similar manual diagnostics for exchange transports.

## Data

Everything the repository ships is synthetic. `scripts/demo_sqlite.py` writes made-up balances
into a throwaway SQLite file; tests use fixtures and stubs only. No exchange keys, wallet
keys, customer data, proxy credentials or production database dumps are part of the
repository, and `.env.example` contains placeholders rather than values.

## Third-party software

PostgreSQL, Redis, CCXT, aiogram, FastAPI, SQLAlchemy, Alembic, Playwright, k6, DeBank SDK
dependencies and the rest of the Python/Node packages listed in `requirements*.txt` and
`package-lock.json` remain under their own licenses and are used as dependencies.
