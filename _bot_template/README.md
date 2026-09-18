# Bot Template

# Шаблон бота на архитектуре Arbitron

Этот шаблон содержит готовую структуру для создания нового Telegram бота.

## Быстрый старт

1. Скопируйте папку `_template` и переименуйте:

   ```bash
   cp -r bots/_template bots/my_new_bot
   ```

2. Обновите конфигурацию в `.env` и `config.toml`

3. Обновите `docker-compose.bots.yml`:

   ```yaml
   my_new_bot:
     build:
       context: ./bots
       dockerfile: my_new_bot/Dockerfile
     container_name: my_new_bot
     # ...
   ```

4. Запустите:
   ```bash
   docker compose -f docker-compose.bots.yml up -d --build my_new_bot
   ```

## Структура

```
_template/
├── __init__.py           # Инициализация пакета
├── main.py               # Точка входа
├── config.py             # Конфигурация из .env и config.toml
├── messages.py           # Тексты сообщений
├── Dockerfile            # Docker образ
├── requirements.txt      # Python зависимости
├── .env.example          # Пример переменных окружения
├── config.toml           # Конфигурация бота
├── database/
│   ├── __init__.py       # Экспорт моделей и CRUD
│   ├── models.py         # SQLAlchemy модели (специфичные для бота)
│   └── crud.py           # CRUD операции
├── handlers/
│   ├── __init__.py
│   └── router.py         # Aiogram роутеры и хендлеры
├── keyboards/
│   ├── __init__.py
│   └── inline.py         # Inline клавиатуры
├── middlewares/
│   ├── __init__.py
│   └── db_session.py     # Middleware для сессии БД
└── services/             # Опционально, если нужна бизнес-логика/API
```

## Общие модули (\_core, \_shared)

### \_core — База данных

```python
from _core.database.models import Base, User, MonitoredChat
from _core.database.user_crud import UserCrud
from _core.database.monitored_chat_crud import MonitoredChatCrud
```

### \_shared — Утилиты

```python
from _shared.utils import format_number, truncate
```

## Модели

Бот использует общую таблицу `users`:

- `id` — внутренний ID
- `telegram_id` — Telegram ID пользователя
- `username`, `first_name`, `last_name`
- `is_premium`, `is_active`
- `created_at`, `updated_at`

Для специфичных данных создавайте свои таблицы с FK на `users.id`.

## Рекомендации

1. **Один файл = одна ответственность**
2. **Используйте UserCrud из \_core** для работы с пользователями
3. **Все тексты в messages.py**
4. **Все клавиатуры в keyboards/**
5. **Бизнес-логика в services/**

## Best practices (актуально на 2026)

1. **Конфиг загружается один раз при старте** (`config.py`, `@lru_cache`).
2. **Fail-fast конфиг**: отсутствующие обязательные ENV → ошибка запуска.
3. **Middleware подключать на `message` и `callback_query` отдельно**:
   - `DbSessionMiddleware`
   - `ConfigMiddleware`
4. **Polling lifecycle**:
   - перед запуском: `delete_webhook(drop_pending_updates=True)`
   - в `finally`: закрыть `storage`, `engine`, `bot.session`
5. **CRUD/модели в snake_case** (`get_or_create`, `user_id`, `created_at`).
