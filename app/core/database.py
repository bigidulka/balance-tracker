import logging
import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()
database_url = settings.resolved_database_url
is_sqlite = database_url.startswith("sqlite+")

if not is_sqlite:
    logger.info(
        "Using PostgreSQL database at %s:%s/%s",
        settings.postgres_host,
        settings.postgres_port,
        settings.postgres_db,
    )

if is_sqlite:
    os.makedirs("data", exist_ok=True)

engine_kwargs = {
    "echo": False,
}

if not is_sqlite:
    engine_kwargs.update(
        {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout,
            "pool_recycle": settings.db_pool_recycle,
            "pool_pre_ping": True,
        }
    )

engine = create_async_engine(database_url, **engine_kwargs)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    if is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    logger.info("Skipping metadata.create_all for PostgreSQL; use Alembic migrations")
