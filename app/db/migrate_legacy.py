"""Read-only import of the legacy Telegram users into the platform table.

The old bot kept ``public.users`` in Supabase: integer credits, ISO text
timestamps, and a ``premium_until`` subscription stamp. The platform keeps a
different ``users`` table in its own schema. This module reads the legacy rows
through psycopg and upserts them into the platform table. It never writes to,
alters, or drops anything in ``public``.

The merge is idempotent, so boot can run it every time and an admin can rerun
it on demand:

* credits — ``max(platform, legacy)``; a rerun never reduces a balance
* banned — OR, a legacy ban is never lifted by an import
* tier — promoted to ``premium`` while ``premium_until`` is in the future
* last_seen_at — the later of the two stamps, falling back to the join date
* created_at — the earlier of the two stamps
* username — filled only when the platform row has no name
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
import structlog
from psycopg.rows import dict_row
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

log = structlog.get_logger("db.migrate")

_LEGACY_SELECT = """
    SELECT user_id, username, joined_utc, last_seen_utc,
           credits, premium_until, is_banned
      FROM public.users
"""


@dataclass(frozen=True)
class MigrationReport:
    """What one import pass did. ``error`` is a short reason, never a URL."""

    read: int = 0
    created: int = 0
    updated: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_ts(value: Any) -> datetime | None:
    """Parse the legacy ISO text (or a real timestamp) into aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def _parse_credits(value: Any) -> Decimal:
    try:
        credits = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return credits if credits > 0 else Decimal("0")


def _parse_username(value: Any) -> str:
    return str(value or "").strip().lstrip("@")[:64]


def _later(current: datetime | None, candidate: datetime | None) -> datetime | None:
    current = _aware(current)
    if candidate is None:
        return current
    if current is None or candidate > current:
        return candidate
    return current


def _earlier(current: datetime | None, candidate: datetime | None) -> datetime | None:
    current = _aware(current)
    if candidate is None:
        return current
    if current is None or candidate < current:
        return candidate
    return current


async def _read_legacy(url: str) -> list[dict[str, Any]]:
    async with (
        await psycopg.AsyncConnection.connect(url, connect_timeout=10) as conn,
        conn.cursor(row_factory=dict_row) as cursor,
    ):
        await cursor.execute(_LEGACY_SELECT)
        return [dict(row) for row in await cursor.fetchall()]


async def migrate_legacy_users(session: AsyncSession, url: str) -> MigrationReport:
    """Upsert every legacy row into the platform ``users`` table.

    Returns an empty report when ``url`` is blank. A read failure is reported,
    not raised: the bot must still boot when Supabase is unreachable.
    """
    if not url:
        return MigrationReport()
    try:
        rows = await _read_legacy(url)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        log.warning("event=legacy_import_read_failed", error=reason)
        return MigrationReport(error=reason)

    now = datetime.now(UTC)
    created = 0
    updated = 0
    for row in rows:
        raw_id = row.get("user_id")
        if raw_id is None:
            continue
        user_id = int(raw_id)
        username = _parse_username(row.get("username"))
        credits = _parse_credits(row.get("credits"))
        joined = _parse_ts(row.get("joined_utc"))
        seen = _parse_ts(row.get("last_seen_utc")) or joined
        premium_until = _parse_ts(row.get("premium_until"))
        premium = premium_until is not None and premium_until > now
        banned = bool(row.get("is_banned"))

        existing = await session.get(User, user_id)
        if existing is None:
            session.add(
                User(
                    id=user_id,
                    username=username,
                    credits=credits,
                    tier="premium" if premium else "free",
                    banned=banned,
                    last_seen_at=seen,
                    created_at=joined or seen or now,
                )
            )
            created += 1
            continue

        existing.credits = max(Decimal(existing.credits or 0), credits)
        existing.banned = bool(existing.banned) or banned
        if premium and existing.tier == "free":
            existing.tier = "premium"
        existing.last_seen_at = _later(existing.last_seen_at, seen)
        existing.created_at = _earlier(existing.created_at, joined) or now
        if username and not existing.username:
            existing.username = username
        updated += 1

    await session.flush()
    report = MigrationReport(read=len(rows), created=created, updated=updated)
    log.info(
        "event=legacy_users_imported",
        read=report.read,
        created=report.created,
        updated=report.updated,
    )
    return report
