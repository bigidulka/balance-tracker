"""Конфигурация бота (загрузка один раз, TOML + ENV)."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import tomllib


@dataclass(frozen=True)
class BotSettings:
    name: str
    admins: list[int]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    redis_url: str
    redis_fsm_url: str
    log_level: str
    bot: BotSettings

    @property
    def bot_name(self) -> str:
        return self.bot.name


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Обязательная переменная окружения {name} не задана")
    return value


def _load_toml() -> dict:
    config_path = Path(__file__).parent / "config.toml"
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as file:
        return tomllib.load(file)


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    config = _load_toml()

    bot_token = _require_env("BOT_TOKEN")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        db_user = _require_env("POSTGRES_USER")
        db_pass = _require_env("POSTGRES_PASSWORD")
        db_host = _require_env("POSTGRES_HOST")
        db_port = _require_env("POSTGRES_PORT")
        db_name = _require_env("POSTGRES_DB")
        database_url = (
            f"postgresql+asyncpg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
        )

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        redis_host = _require_env("REDIS_HOST")
        redis_port = _require_env("REDIS_PORT")
        redis_db = os.getenv("REDIS_DB", "0")
        redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"
        redis_fsm_db = os.getenv("REDIS_FSM_DB")
        redis_fsm_url = (
            f"redis://{redis_host}:{redis_port}/{redis_fsm_db}"
            if redis_fsm_db
            else redis_url
        )
    else:
        redis_fsm_url = redis_url

    redis_fsm_url = os.getenv("REDIS_FSM_URL", redis_fsm_url)

    bot_section = config.get("bot", {})
    admins_raw = bot_section.get("admins") or []
    bot_settings = BotSettings(
        name=bot_section.get("name", "template_bot"),
        admins=[int(value) for value in admins_raw],
    )

    admins_env = os.getenv("BOT_ADMINS", "").strip()
    if admins_env:
        bot_settings = BotSettings(
            name=bot_settings.name,
            admins=[int(value.strip()) for value in admins_env.split(",") if value.strip()],
        )

    return Settings(
        bot_token=bot_token,
        database_url=database_url,
        redis_url=redis_url,
        redis_fsm_url=redis_fsm_url,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        bot=bot_settings,
    )


settings = load_settings()
