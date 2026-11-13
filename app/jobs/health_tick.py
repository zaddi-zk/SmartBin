"""Health tick: recompute gate_state from real samples (never a constant)."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from app.db import repo
from app.db.session import session_scope


async def health_tick(stop: asyncio.Event, interval_s: int = 60) -> None:
    while not stop.is_set():
        async with session_scope() as session:
            for gate in await repo.list_gates(session):
                health = await repo.load_health(session, gate.id)
                state = await repo.ensure_gate_state(session, gate.id)
                state.p50_ms = health.p50()
                state.p95_ms = health.p95()
                success = health.success_pct()
                state.success_pct = Decimal(str(success)) if success is not None else None
                state.samples = health.total
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue
