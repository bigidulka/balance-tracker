from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.database import Base
from app.models import *  # noqa: F401,F403


def _default_sqlite_url() -> str:
    return f"sqlite+aiosqlite:///{PROJECT_ROOT / 'data' / 'balance_tracker.db'}"


def _default_postgres_url() -> str:
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "balance_tracker")
    user = os.getenv("POSTGRES_USER", "balance_tracker")
    password = os.getenv("POSTGRES_PASSWORD", "balance_tracker")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def _head_revision() -> str:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    return str(script.get_current_head())


def _table_names(tables: Iterable[Any]) -> str:
    return ", ".join(table.name for table in tables)


async def _table_count(engine: AsyncEngine, table: Any) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(select(func.count()).select_from(table))
        return int(result.scalar_one() or 0)


async def _target_has_data(engine: AsyncEngine) -> bool:
    for table in Base.metadata.sorted_tables:
        if await _table_count(engine, table) > 0:
            return True
    return False


async def _create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        await conn.execute(text("DELETE FROM alembic_version"))
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": _head_revision()},
        )


async def _clear_target(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(delete(table))
        await conn.execute(text("DELETE FROM alembic_version"))


async def _copy_table(
    source: AsyncEngine,
    target: AsyncEngine,
    table: Any,
    *,
    batch_size: int,
) -> tuple[int, int]:
    source_count = await _table_count(source, table)
    copied = 0

    if source_count == 0:
        return 0, 0

    column_names = [column.name for column in table.columns]
    offset = 0
    while offset < source_count:
        async with source.connect() as source_conn:
            rows = (
                await source_conn.execute(
                    select(table).order_by(*table.primary_key.columns).limit(batch_size).offset(offset)
                )
            ).mappings().all()

        payload = [
            {name: row[name] for name in column_names}
            for row in rows
        ]
        if payload:
            async with target.begin() as target_conn:
                await target_conn.execute(insert(table), payload)
            copied += len(payload)
        offset += batch_size

    return source_count, copied


async def _reset_postgres_sequences(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            pk_columns = []
            for column in table.primary_key.columns:
                try:
                    if column.type.python_type is int:
                        pk_columns.append(column)
                except NotImplementedError:
                    continue
            for column in pk_columns:
                sequence = (
                    await conn.execute(
                        text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                        {
                            "table_name": table.name,
                            "column_name": column.name,
                        },
                    )
                ).scalar_one_or_none()
                if not sequence:
                    continue
                max_id = (
                    await conn.execute(
                        select(func.coalesce(func.max(column), 0)).select_from(table)
                    )
                ).scalar_one()
                sequence_value = int(max_id) if int(max_id) > 0 else 1
                await conn.execute(
                    text("SELECT setval(CAST(:sequence AS regclass), :value, :is_called)"),
                    {
                        "sequence": sequence,
                        "value": sequence_value,
                        "is_called": int(max_id) > 0,
                    },
                )


async def _verify_counts(source: AsyncEngine, target: AsyncEngine) -> None:
    mismatches: list[str] = []
    for table in Base.metadata.sorted_tables:
        source_count = await _table_count(source, table)
        target_count = await _table_count(target, table)
        if source_count != target_count:
            mismatches.append(f"{table.name}: sqlite={source_count} postgres={target_count}")
    if mismatches:
        raise RuntimeError("Row count mismatch after migration: " + "; ".join(mismatches))


async def migrate(args: argparse.Namespace) -> None:
    source = create_async_engine(args.source_url)
    target = create_async_engine(args.target_url)
    try:
        await _create_schema(target)
        if await _target_has_data(target):
            if not args.truncate:
                raise RuntimeError(
                    "Target database already contains data. Re-run with --truncate to replace it."
                )
            await _clear_target(target)
            await _create_schema(target)

        print(f"Migrating tables: {_table_names(Base.metadata.sorted_tables)}")
        for table in Base.metadata.sorted_tables:
            source_count, copied = await _copy_table(
                source,
                target,
                table,
                batch_size=args.batch_size,
            )
            print(f"{table.name}: {copied}/{source_count}")

        await _reset_postgres_sequences(target)
        await _verify_counts(source, target)
        print("Migration complete: row counts verified")
    finally:
        await source.dispose()
        await target.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate Balance Tracker SQLite data to PostgreSQL")
    parser.add_argument("--source-url", default=_default_sqlite_url())
    parser.add_argument("--target-url", default=_default_postgres_url())
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--truncate", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(migrate(parse_args()))
