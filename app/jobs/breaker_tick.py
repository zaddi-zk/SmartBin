"""Breaker tick: OPEN -> HALF_OPEN when the backoff expires."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.db import repo
from app.db.session import session_scope
from app.runtime import runtime


async def breaker_tick(stop: asyncio.Event, interval_s: int = 5) -> None:
    while not stop.is_set():
        executor = runtime().executor
        async with session_scope() as session:
            for gate_id, breaker in list(executor.breakers.items()):
                before = breaker.state
                breaker.tick()
                if breaker.state != before:
                    state = await repo.ensure_gate_state(session, gate_id)
                    state.state = "HALF_OPEN" if breaker.state.value == "HALF_OPEN" else "HEALTHY"
                    state.next_probe_at = (
                        datetime.now(UTC) + timedelta(seconds=breaker.backoff_s)
                        if breaker.state.value == "HALF_OPEN"
                        else None
                    )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue
