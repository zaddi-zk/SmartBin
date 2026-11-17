"""Keep the bot profile's monthly-user line current.

Telegram renders the short description under the bot name. The legacy bot set
it once to a count that then went stale (``4 monthly users``). This module
recomputes the real 30-day active count and refreshes the profile at boot and
once an hour.

Only the short description is dynamic. The long description stays the fixed
brand text.
"""

from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot

from app.db import repo
from app.db.session import session_scope

log = structlog.get_logger("tg.description")

UPDATE_INTERVAL = 3600  # seconds between profile refreshes


def short_description(monthly: int) -> str:
    """The public profile line. Mirrors the legacy wording exactly."""
    return f"{monthly} monthly users"


async def update_description(bot: Bot, monthly: int | None = None) -> str:
    """Write the current count to the default and English short description.

    Returns the text that was written. A Telegram failure is logged and never
    raised: the profile line is cosmetic and must not break a tick.
    """
    if monthly is None:
        async with session_scope() as session:
            monthly = await repo.monthly_active(session)
    text = short_description(monthly)
    for language in (None, "en"):
        try:
            await bot.set_my_short_description(
                short_description=text, language_code=language
            )
        except Exception as exc:
            log.warning(
                "event=short_description_failed",
                language=language or "default",
                error=str(exc),
            )
    return text


async def description_tick(
    bot: Bot, stop: asyncio.Event, interval_s: int = UPDATE_INTERVAL
) -> None:
    while not stop.is_set():
        try:
            text = await update_description(bot)
            log.info("event=description_updated", short=text)
        except Exception as exc:
            # A background tick must never kill the bot.
            log.warning("event=description_tick_failed", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue
