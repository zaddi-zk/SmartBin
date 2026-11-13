"""Repository functions. All SQL lives here so the executor stays testable."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.health import RollingHealth, Sample
from app.db.models import (
    ApiKey,
    CanaryCard,
    Check,
    Gate,
    GateSampleRow,
    GateStateRow,
    Proxy,
    User,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- users -----------------------------------------------------------------
async def ensure_user(session: AsyncSession, user_id: int, lang: str = "en") -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, lang=lang, credits=Decimal("0"))
        session.add(user)
        await session.flush()
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def set_sticky_gate(session: AsyncSession, user_id: int, gate_id: str) -> None:
    await session.execute(update(User).where(User.id == user_id).values(sticky_gate=gate_id))


# --- gates -----------------------------------------------------------------
async def list_gates(session: AsyncSession, lane: str | None = None) -> list[Gate]:
    stmt = select(Gate)
    if lane:
        stmt = stmt.where(Gate.lane == lane)
    stmt = stmt.order_by(Gate.sort_hint, Gate.label)
    return list((await session.scalars(stmt)).all())


async def get_gate(session: AsyncSession, gate_id: str) -> Gate | None:
    return await session.get(Gate, gate_id)


async def gate_states(session: AsyncSession) -> dict[str, GateStateRow]:
    rows = (await session.scalars(select(GateStateRow))).all()
    return {row.gate_id: row for row in rows}


async def ensure_gate_state(session: AsyncSession, gate_id: str) -> GateStateRow:
    row = await session.get(GateStateRow, gate_id)
    if row is None:
        row = GateStateRow(gate_id=gate_id)
        session.add(row)
        await session.flush()
    return row


async def update_gate_state(session: AsyncSession, gate_id: str, **values: Any) -> None:
    await session.execute(
        update(GateStateRow).where(GateStateRow.gate_id == gate_id).values(**values)
    )


# --- keys ------------------------------------------------------------------
async def add_key(
    session: AsyncSession,
    gate_id: str,
    secret: str,
    country: str = "US",
    quota_left: int | None = None,
) -> ApiKey:
    key = ApiKey(gate_id=gate_id, secret=secret, country=country, quota_left=quota_left)
    session.add(key)
    await session.flush()
    return key


async def keys_for_gate(session: AsyncSession, gate_id: str) -> list[ApiKey]:
    stmt = select(ApiKey).where(ApiKey.gate_id == gate_id).order_by(ApiKey.id)
    return list((await session.scalars(stmt)).all())


async def pending_keys(session: AsyncSession) -> list[ApiKey]:
    return list((await session.scalars(select(ApiKey).where(ApiKey.state == "PENDING"))).all())


async def pick_key(session: AsyncSession, gate_id: str) -> ApiKey | None:
    """READY, quota left, not benched; least-recently-used first."""
    now = utcnow()
    stmt = (
        select(ApiKey)
        .where(
            ApiKey.gate_id == gate_id,
            ApiKey.state == "READY",
            ApiKey.benched_until.is_(None) | (ApiKey.benched_until <= now),
        )
        .order_by(ApiKey.last_used_at.asc().nulls_first(), ApiKey.id.asc())
        .limit(1)
    )
    key = await session.scalar(stmt)
    if key is not None:
        key.last_used_at = now
        if key.quota_left is not None:
            key.quota_left -= 1
            if key.quota_left <= 0:
                key.state = "BENCHED"
                key.benched_until = now + timedelta(seconds=300)
    return key


async def bench_key(session: AsyncSession, key_id: int, reason: str, seconds: int = 300) -> None:
    key = await session.get(ApiKey, key_id)
    if key is None:
        return
    key.benches += 1
    key.state = "DEAD" if key.benches >= 3 else "BENCHED"
    key.benched_until = None if key.state == "DEAD" else utcnow() + timedelta(seconds=seconds)
    key.livemode = False if key.state == "DEAD" else key.livemode


# --- checks + samples ------------------------------------------------------
async def create_check(
    session: AsyncSession,
    *,
    user_id: int,
    gate_id: str,
    pan_masked: str,
    pan_hash: str,
    verdict: str,
    code: str,
    decline_code: str,
    avs: str,
    cvc: str,
    three_ds: str,
    latency_ms: int,
    provider_ref: str,
    amount_cents: int,
    debited: Decimal,
) -> Check:
    check = Check(
        user_id=user_id,
        gate_id=gate_id,
        pan_masked=pan_masked,
        pan_hash=pan_hash,
        verdict=verdict,
        code=code,
        decline_code=decline_code,
        avs=avs,
        cvc=cvc,
        three_ds=three_ds,
        latency_ms=latency_ms,
        provider_ref=provider_ref,
        amount_cents=amount_cents,
        debited=debited,
    )
    session.add(check)
    await session.flush()
    return check


async def add_sample(session: AsyncSession, gate_id: str, ms: int, ok: bool) -> None:
    session.add(GateSampleRow(gate_id=gate_id, ms=ms, ok=ok))


async def load_health(session: AsyncSession, gate_id: str, hours: int = 24) -> RollingHealth:
    cutoff = utcnow() - timedelta(hours=hours)
    stmt = (
        select(GateSampleRow)
        .where(GateSampleRow.gate_id == gate_id, GateSampleRow.at >= cutoff)
        .order_by(GateSampleRow.at.asc())
        .limit(5000)
    )
    health = RollingHealth()
    health.extend(
        Sample(at=row.at, ms=row.ms, ok=row.ok) for row in (await session.scalars(stmt)).all()
    )
    return health


async def recent_checks(session: AsyncSession, user_id: int, limit: int = 10) -> Sequence[Check]:
    stmt = (
        select(Check).where(Check.user_id == user_id).order_by(Check.created_at.desc()).limit(limit)
    )
    return (await session.scalars(stmt)).all()


async def duplicate_check(
    session: AsyncSession, user_id: int, pan_hash: str, seconds: int = 60
) -> Check | None:
    cutoff = utcnow() - timedelta(seconds=seconds)
    stmt = (
        select(Check)
        .where(
            Check.user_id == user_id,
            Check.pan_hash == pan_hash,
            Check.created_at >= cutoff,
        )
        .order_by(Check.created_at.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def check_counts(session: AsyncSession, user_id: int) -> dict[str, int]:
    stmt = (
        select(Check.verdict, func.count()).where(Check.user_id == user_id).group_by(Check.verdict)
    )
    rows = (await session.execute(stmt)).all()
    return {str(verdict): int(count) for verdict, count in rows}


# --- proxies ---------------------------------------------------------------
async def alive_proxies(session: AsyncSession, country: str = "") -> list[Proxy]:
    stmt = select(Proxy).where(Proxy.alive.is_(True))
    if country:
        stmt = stmt.where(Proxy.country == country)
    result = await session.scalars(stmt.order_by(Proxy.latency_ms.asc().nulls_last()))
    return list(result.all())


async def add_proxy(session: AsyncSession, url: str, country: str = "") -> Proxy:
    existing = await session.scalar(select(Proxy).where(Proxy.url == url))
    if existing is not None:
        return existing
    proxy = Proxy(url=url, country=country)
    session.add(proxy)
    await session.flush()
    return proxy


async def canary_cards(session: AsyncSession, country: str = "") -> list[CanaryCard]:
    stmt = select(CanaryCard).where(CanaryCard.alive.is_(True))
    if country:
        stmt = stmt.where(CanaryCard.country == country)
    return list((await session.scalars(stmt)).all())
