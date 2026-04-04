---
name: telegram-bot-ui-architect
description: Проектирует и ревьюит UI/UX Telegram-бота на aiogram в strict single-message edit-only паттерне с route-aware Back, FSM-вводом и безопасным formatting через entities. Используй при добавлении экранов, меню, callback-flow и любых изменений навигации.
argument-hint: "[задача] [раздел/экран] [ограничения/контекст]"
disable-model-invocation: true
allowed-tools: Agent, Read, Edit, Write, Glob, Grep, Bash
---

# Telegram Bot UI Architect

## Intent
Сгенерировать или отрефакторить Telegram UI так, чтобы он строго соответствовал правилам:
- один пользовательский поток = одно главное сообщение;
- inline-only навигация;
- edit-only переходы;
- route-aware back по реальному пути;
- FSM только для ввода/пошаговых сценариев;
- тонкие handlers + service/screen-builder/middleware/repository декомпозиция;
- безопасное форматирование через `aiogram.utils.formatting`.

## Inputs
- ARGUMENTS: $ARGUMENTS
- Если контекста не хватает, сначала запроси:
  1) какие файлы/модуль менять;
  2) какие экраны и переходы нужны;
  3) какие состояния FSM и какие поля ввода;
  4) где уже реализован middleware-инжект зависимостей.

## Execution protocol
1) Сначала найди текущую реализацию экранов/навигации/FSM/middleware.
2) Составь screen map:
   - `screen_id`
   - `parent(s)`
   - `entry route`
   - `back behavior`
3) Реализуй route context и navigation stack в state (не только current screen).
4) Реализуй forward/back через edit текущего сообщения.
5) Для вводов: FSM wait-state -> удалить пользовательское сообщение -> edit главного сообщения.
6) Убедись, что хэндлеры не обращаются к БД напрямую: только зависимости из middleware.
7) Переведи текстовые экраны на `aiogram.utils.formatting` (`Text`, `Bold`, `as_section`, `as_key_value`, `BlockQuote`/совместимый entity fallback).
8) Для кнопок используй premium custom emoji `icon_custom_emoji_id` (без обычных emoji в тексте кнопок).
9) При stale/invalid callback и state desync выполняй мягкое восстановление интерфейса edit-ом того же сообщения.

## Output format
Возвращай ответ строго в секциях:
1. **Что сделано**
2. **Архитектура**
   - screen map
   - state schema
   - back/route logic
3. **Изменения в коде**
   - файлы и ключевые фрагменты
4. **Проверка соответствия правилам** (чеклист pass/fail)
5. **Риски/заметки** (если есть ограничения версии aiogram)

## Hard constraints
- Нельзя спамить новыми сообщениями в навигации.
- Нельзя строить universal Back по одному `screen_id` при multi-parent входах.
- Нельзя делать DB access в handlers.
- Нельзя грузить user/context в handlers вручную.
- Нельзя использовать parse_mode-строки как основной способ форматирования, если доступен formatting API.

## Premium emoji policy
- Inline/reply кнопки: только `icon_custom_emoji_id`.
- Текст сообщений: безопасные entities; `<tg-emoji ...>` допустим только если это уже принятый и безопасный проектный стандарт.
- Используй карту emoji из `reference.md`.

## Required self-check before final
- [ ] Single-message UI соблюден
- [ ] Inline-only navigation соблюдена
- [ ] Edit-only response pattern соблюден
- [ ] Route-aware back работает при multi-parent
- [ ] FSM input flow удаляет user message
- [ ] Handlers thin, без DB access
- [ ] Логика в service/screen builder
- [ ] Middleware инжектит данные/репозитории/пользователя
- [ ] Безопасное formatting API
- [ ] Premium emoji policy в кнопках соблюдена

## Examples
- `/telegram-bot-ui-architect Добавь раздел "Настройки уведомлений" с 3 уровнями, route-aware back и edit-only UX`
- `/telegram-bot-ui-architect Перепиши экран профиля: formatting через Text/Bold/as_section, ввод имени через FSM и удаление user message`
- `/telegram-bot-ui-architect Проведи ревью навигации и исправь stale callback recovery без новых сообщений`

## Additional resources
- Правила и карта premium emoji: `reference.md`
- Шаблоны screen map/state/back/FSM: `examples.md`
