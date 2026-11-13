"""Charge lane: one real capture attempt."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.parse import Card
from app.db.models import User
from app.exec.check import CheckExecutor, CheckOutcome


async def charge(
    executor: CheckExecutor,
    session: AsyncSession,
    *,
    user: User,
    card: Card,
    gate_id: str | None = None,
    bin_iso2: str = "",
) -> CheckOutcome:
    return await executor.run(
        session,
        user=user,
        card=card,
        lane="charge",
        gate_id=gate_id,
        bin_iso2=bin_iso2,
    )
