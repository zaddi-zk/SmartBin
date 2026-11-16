"""Proxy tick (section 13): keep the pool fresh and every user reachable.

The job runs forever in the background. Each pass tests the least-recently
checked rows, then gives any user without live egress a fast proxy. Rows are
never handed to a card check until they have answered ``api.ipify.org``.
"""

from __future__ import annotations

import asyncio

import structlog

from app.db.session import session_scope
from app.runtime import runtime

log = structlog.get_logger("jobs.proxy")


def _interval_s(default_hours: int) -> int:
    # At least twice an hour, but never faster than every ten minutes. A 24h
    # refresh window therefore keeps an 8k pool warm well inside the window.
    return max(600, min(default_hours * 3600, 1800))


async def proxy_tick(
    stop: asyncio.Event,
    interval_s: int | None = None,
    *,
    batch: int = 200,
) -> None:
    settings = runtime().settings
    interval = interval_s if interval_s is not None else _interval_s(settings.proxy_refresh_hours)

    while not stop.is_set():
        try:
            async with session_scope() as session:
                pool = runtime().proxies
                alive, tested = await pool.refresh(session, limit=batch)
                assigned = await pool.auto_assign(session)
            log.info(
                "event=proxy_tick",
                alive=alive,
                tested=tested,
                assigned=assigned,
            )
        except Exception as exc:
            # A background tick must never kill the bot.
            log.warning("event=proxy_tick_failed", error=str(exc))

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
