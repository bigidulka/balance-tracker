# aiogram-bot-ui-architect memory

## Stable project patterns
- Bot UI runtime is in `bot/` (not `app/`):
  - routing/orchestration: `/home/fsdf1234/Projects/balance-tracker/bot/handlers/router.py`
  - text/screen builders via `aiogram.utils.formatting`: `/home/fsdf1234/Projects/balance-tracker/bot/messages.py`
  - inline keyboards: `/home/fsdf1234/Projects/balance-tracker/bot/keyboards/inline.py`
- `NO_BACKEND_UI_MODE` is a first-class env flag:
  - parsed in `/home/fsdf1234/Projects/balance-tracker/bot/config.py`
  - mock/empty payload substitution is implemented in `/home/fsdf1234/Projects/balance-tracker/bot/api_client.py`
- Premium button standard uses `icon_custom_emoji_id` in keyboard builders; centralized icon map in `bot/keyboards/inline.py`.
- Single-message UX baseline is edit-oriented callbacks (`callback.message.edit_text(...)`), with one initial `message.answer(...)` on `/start`.

## Recurring pitfalls
- Router can drift from message-builder API names. Verify handlers call actual functions from `bot/messages.py` (e.g., avoid stale names like `format_dashboard_summary` if absent).
- Syntax/import regressions in router are easy to miss; run `python -m compileall /home/fsdf1234/Projects/balance-tracker/bot` after UI edits.
