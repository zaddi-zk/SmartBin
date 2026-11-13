"""Async engine/session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = load_settings().db_url
        kwargs: dict[str, object] = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs = {}
        _engine = create_async_engine(url, **kwargs)
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


async def init_db() -> None:
    async with engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
