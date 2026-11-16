"""Legacy Supabase import: field mapping, safe merge, and idempotency.

The real source is Postgres; these tests replace only the read step so the
merge logic runs against the same SQLite session the rest of the suite uses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.config import Settings, psycopg_url
from app.db import repo
from app.db.migrate_legacy import migrate_legacy_users
from app.db.models import User
from sqlalchemy.ext.asyncio import AsyncSession

LEGACY_URL = "postgresql://legacy:secret@example.invalid/postgres?sslmode=require"


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_id": 555,
        "username": "legacy_user",
        "joined_utc": "2026-11-01T10:00:00Z",
        "last_seen_utc": "2026-11-10T12:00:00Z",
        "credits": 40,
        "premium_until": "",
        "is_banned": 0,
    }
    base.update(overrides)
    return base


@pytest.fixture
def legacy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Point the importer at an in-memory row set the test can mutate."""
    state: dict[str, Any] = {"rows": [], "error": None}

    async def fake_read(url: str) -> list[dict[str, Any]]:
        if state["error"] is not None:
            raise state["error"]
        return state["rows"]

    monkeypatch.setattr("app.db.migrate_legacy._read_legacy", fake_read)
    return state


async def test_maps_legacy_fields_into_a_new_user(
    session: AsyncSession, legacy: dict[str, Any]
) -> None:
    legacy["rows"] = [_row()]
    report = await migrate_legacy_users(session, LEGACY_URL)

    user = await session.get(User, 555)
    assert report.read == 1 and report.created == 1 and report.updated == 0
    assert user is not None
    assert user.username == "legacy_user"
    assert Decimal(user.credits) == Decimal("40")
    assert user.tier == "free"
    assert user.banned is False
    assert user.last_seen_at is not None
    assert user.created_at is not None


async def test_premium_until_promotes_the_tier(
    session: AsyncSession, legacy: dict[str, Any]
) -> None:
    future = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    legacy["rows"] = [_row(user_id=556, premium_until=future)]
    await migrate_legacy_users(session, LEGACY_URL)

    user = await session.get(User, 556)
    assert user is not None and user.tier == "premium"


async def test_expired_premium_does_not_downgrade_an_existing_tier(
    session: AsyncSession, legacy: dict[str, Any]
) -> None:
    session.add(User(id=557, credits=Decimal("0"), tier="premium"))
    await session.flush()
    legacy["rows"] = [_row(user_id=557, premium_until="2020-01-01T00:00:00Z")]
    await migrate_legacy_users(session, LEGACY_URL)

    user = await session.get(User, 557)
    assert user is not None and user.tier == "premium"


async def test_merge_never_reduces_credits_and_never_lifts_a_ban(
    session: AsyncSession, legacy: dict[str, Any]
) -> None:
    session.add(User(id=558, username="", credits=Decimal("100"), banned=False))
    await session.flush()
    legacy["rows"] = [_row(user_id=558, credits=40, is_banned=1, username="new_name")]
    report = await migrate_legacy_users(session, LEGACY_URL)

    user = await session.get(User, 558)
    assert report.created == 0 and report.updated == 1
    assert user is not None
    assert Decimal(user.credits) == Decimal("100")
    assert user.banned is True
    assert user.username == "new_name"


async def test_rerun_is_idempotent(
    session: AsyncSession, legacy: dict[str, Any]
) -> None:
    legacy["rows"] = [_row(user_id=559, last_seen_utc="2026-11-10T12:00:00Z")]
    first = await migrate_legacy_users(session, LEGACY_URL)
    await session.flush()
    second = await migrate_legacy_users(session, LEGACY_URL)
    await session.flush()

    user = await session.get(User, 559)
    assert first.created == 1
    assert second.created == 0 and second.updated == 1
    assert user is not None
    assert Decimal(user.credits) == Decimal("40")
    assert user.username == "legacy_user"


async def test_newest_activity_and_earliest_join_win(
    session: AsyncSession, legacy: dict[str, Any]
) -> None:
    session.add(
        User(
            id=560,
            credits=Decimal("0"),
            last_seen_at=datetime(2026, 11, 12, tzinfo=UTC),
            created_at=datetime(2026, 11, 2, tzinfo=UTC),
        )
    )
    await session.flush()
    legacy["rows"] = [
        _row(
            user_id=560,
            joined_utc="2026-10-20T00:00:00Z",
            last_seen_utc="2026-11-14T00:00:00Z",
        )
    ]
    await migrate_legacy_users(session, LEGACY_URL)

    user = await session.get(User, 560)
    assert user is not None
    assert user.last_seen_at is not None
    assert user.created_at is not None
    assert user.last_seen_at.replace(tzinfo=UTC) >= datetime(2026, 11, 14, tzinfo=UTC)
    assert user.created_at.replace(tzinfo=UTC) == datetime(2026, 10, 20, tzinfo=UTC)


async def test_missing_last_seen_falls_back_to_join_date(
    session: AsyncSession, legacy: dict[str, Any]
) -> None:
    legacy["rows"] = [_row(user_id=561, last_seen_utc="", joined_utc="2026-11-05T00:00:00Z")]
    await migrate_legacy_users(session, LEGACY_URL)

    user = await session.get(User, 561)
    assert user is not None and user.last_seen_at is not None


async def test_blank_url_is_a_noop(session: AsyncSession, legacy: dict[str, Any]) -> None:
    legacy["rows"] = [_row()]
    report = await migrate_legacy_users(session, "")
    assert report.read == 0 and report.ok
    assert await session.get(User, 555) is None


async def test_read_failure_is_reported_not_raised(
    session: AsyncSession, legacy: dict[str, Any]
) -> None:
    legacy["error"] = RuntimeError("connection refused")
    report = await migrate_legacy_users(session, LEGACY_URL)
    assert report.ok is False
    assert "connection refused" in report.error


async def test_username_is_kept_on_interaction(
    session: AsyncSession,
) -> None:
    user = await repo.ensure_user(session, 562, username="first")
    await repo.touch_user(session, 562, "second")
    await repo.touch_user(session, 562, "")
    await session.flush()

    assert user.username == "second"


def test_legacy_source_url_ignores_the_platform_url() -> None:
    same = Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        db_url="postgresql+asyncpg://u:p@h/db",
        legacy_db_url="postgresql://u:p@h/db",
    )
    assert same.legacy_source_url == ""


def test_legacy_source_url_reads_public_on_a_shared_database() -> None:
    # On a Postgres cutover the platform owns `smartbin`, so `public.users`
    # is still the legacy source even though the host is the same.
    same = Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        db_url="postgresql+asyncpg://u:p@h/db?sslmode=require",
        db_schema="smartbin",
        legacy_db_url="postgresql://u:p@h/db?sslmode=require",
    )
    assert same.legacy_source_url.startswith("postgresql://u:p@h/db")


def test_legacy_source_url_reads_a_distinct_postgres() -> None:
    settings = Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        db_url="sqlite+aiosqlite:///./venice.db",
        legacy_db_url="postgres://u:p@h/db?sslmode=require",
    )
    assert settings.legacy_source_url == "postgresql://u:p@h/db?sslmode=require"


def test_psycopg_url_strips_sqlalchemy_driver() -> None:
    assert psycopg_url("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"
    assert psycopg_url("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"
