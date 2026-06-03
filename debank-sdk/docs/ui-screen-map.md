# UI Screen Map

## Home
- `screen_id`: `home`
- Entry: `/start`, `nav:home`, fallback after root recovery
- Root mode: `photo`
- Buttons:
  - `Мои кошельки` -> `nav:watchlist`
  - `Быстрый поиск` -> `action:quick_lookup`

## Watchlist
- `screen_id`: `watchlist`
- Entry: `nav:watchlist`
- Root mode: `text`
- Buttons:
  - `<wallet label/address>` -> `wallet:open:{address}`
  - `Добавить кошелёк` -> `action:add_watch`
  - `Домой` -> `nav:home`

## Wallet Detail
- `screen_id`: `wallet_detail`
- Entry: tracked wallet click, quick lookup, direct address input
- Root mode: `text`
- Buttons:
  - `Портфель` -> `portfolio:open:{address}`
  - `Транзакции` -> `tx:open:{address}`
  - `Переименовать` -> `wallet:rename:{address}`
  - `Обновить` -> `wallet:refresh:{address}`
  - `Добавить в трек` / `Убрать из трека` -> `watch:add:{address}` / `watch:remove:{address}`
  - `Назад` -> `nav:back`
  - `Домой` -> `nav:home`

## Portfolio Detail
- `screen_id`: `portfolio_detail`
- Entry: `portfolio:open:{address}`
- Root mode: `text`
- Buttons:
  - `Назад` / `Вперёд` -> `portfolio:prev` / `portfolio:next`
  - `Транзакции` -> `tx:open:{address}`
  - `К кошельку` -> `wallet:open:{address}`
  - `Назад` -> `nav:back`
  - `Домой` -> `nav:home`

## Transactions Detail
- `screen_id`: `transactions_detail`
- Entry: `tx:open:{address}`
- Root mode: `text`
- Page size: `5`
- Buttons:
  - `Назад` / `Вперёд` -> `tx:prev` / `tx:next`
  - `Скрыть scam` / `Показать scam` -> `tx:toggle_scam`
  - `Портфель` -> `portfolio:open:{address}`
  - `К кошельку` -> `wallet:open:{address}`
  - `Назад` -> `nav:back`
  - `Домой` -> `nav:home`

## Quick Lookup Input
- FSM: `WalletFlow.waiting_wallet_input`
- Mode: `quick_lookup`
- Trigger: `action:quick_lookup`
- Buttons:
  - `Назад` -> `nav:back`
  - `Домой` -> `nav:home`
- Result:
  - valid address -> `wallet_detail`
  - invalid address -> same screen with inline error

## Add Wallet Input
- FSM: `WalletFlow.waiting_wallet_input`
- Mode: `add_watch`
- Trigger: `action:add_watch`
- Buttons:
  - `Назад` -> `nav:back`
  - `Домой` -> `nav:home`
- Result:
  - valid address -> add to watchlist and open `wallet_detail`
  - invalid address -> same screen with inline error

## Rename Label Input
- FSM: `WalletFlow.waiting_wallet_label`
- Trigger: `wallet:rename:{address}`
- Buttons:
  - `Назад` -> `nav:back`
  - `Домой` -> `nav:home`
- Result:
  - valid label -> save and return to `wallet_detail`
  - invalid label -> same screen with inline error

## Back Rules
- `home` -> stays on `home`
- `watchlist` -> returns to `home`
- `wallet_detail` -> returns to actual previous screen from `nav_stack`
- `portfolio_detail` -> returns to actual previous screen from `nav_stack`
- `transactions_detail` -> returns to actual previous screen from `nav_stack`
- input states:
  - rename -> return to wallet detail of edited address
  - add/search input -> return to previous route or `home`

## Recovery Rules
- If `main_message_id` is missing, router bootstraps a fresh home root and restores state.
- If callback arrives from an old message, router redirects rendering to the current root message instead of crashing.
- Only `home` is allowed to own the photo banner root; every other screen is forced back to text root.
