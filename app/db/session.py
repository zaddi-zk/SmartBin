"""Async engine/session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import load_settings
from app.db.models import Base

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def schema_for_url(url: str, schema: str) -> str:
    """Postgres must name an isolated schema; SQLite has no schemas."""
    if is_sqlite(url):
        return ""
    schema = schema.strip().lower()
    if not schema:
        raise RuntimeError(
            "Postgres needs DB_SCHEMA set to a dedicated schema (for example "
            "'smartbin'). The legacy tables live in public; sharing them would "
            "corrupt both stacks."
        )
    return schema


def engine_kwargs(url: str, schema: str) -> dict[str, object]:
    """Connection options: SQLite keeps defaults, Postgres pins search_path."""
    if is_sqlite(url):
        return {}
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    isolated = schema_for_url(url, schema)
    kwargs["connect_args"] = {
        "server_settings": {"search_path": f"{isolated},public"},
    }
    return kwargs


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = load_settings()
        _engine = create_async_engine(
            settings.db_url,
            **engine_kwargs(settings.db_url, settings.db_schema),
        )
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(engine(), expire_on_commit=False)
    return _factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _add_missing_columns(sync_conn: Any) -> None:
    """create_all never ALTERs; add late columns for DBs created before them."""
    from sqlalchemy import inspect

    names = {column["name"] for column in inspect(sync_conn).get_columns("users")}
    if "last_seen_at" not in names:
        sync_conn.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN last_seen_at TIMESTAMP"
        )


async def init_db() -> None:
    settings = load_settings()
    async with engine().begin() as connection:
        if not is_sqlite(settings.db_url):
            schema = schema_for_url(settings.db_url, settings.db_schema)
            # CREATE SCHEMA before create_all, otherwise the first missing
            # schema silently falls through to public and collides with the
            # legacy tables.
            await connection.execute(
                text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            )
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_add_missing_columns)
