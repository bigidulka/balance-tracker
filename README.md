# Balance Tracker MVP

Current MVP runtime surface:

- `app/`: FastAPI API, balance refresh worker, exchange and wallet services.
- `bot/`: Telegram bot UI and notification loops.
- `scripts/probes/`: manual diagnostics for balance transports and normalization.
- `scripts/research/`: one-off reverse-engineering helpers, not part of runtime.
- `tests/`: regression coverage for runtime and gateway behavior.

Non-runtime research artifacts were moved under `scripts/` so the project root stays focused on the deployable application.

Main entrypoints:

- API: `python -m app.main`
- Worker: `python -m app.worker`
- Bot: `python -m bot.main`
