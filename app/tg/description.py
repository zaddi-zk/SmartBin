"""Keep the bot profile's monthly-user line current.

Telegram maps the two profile fields like this:

* long description (``setMyDescription``) — the line **under the bot name**
* short description (``setMyShortDescription``) — the "(i) Description" row

The legacy bot published the user count under the name and kept the tagline
in the Description row. That is the layout restored here: the count is live,
the tagline is fixed.
"""

from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot

from app.db import repo
from app.db.session import session_scope

log = structlog.get_logger("tg.description")

UPDATE_INTERVAL = 3600  # seconds between profile refreshes
LANGUAGES: tuple[str | None, ...] = (None, "en")

# The "(i) Description" row and the public t.me share preview.
SHORT_DESCRIPTION = "CC Checker & Killer 🔪"


def profile_text(monthly: int) -> str:
    """The line under the bot name. Matches the legacy wording."""
    return f"{monthly} monthly users"


async def update_description(bot: Bot, monthly: int | None = None) -> str:
    """Publish the live count under the name and restore the tagline.

    Returns the count line. A Telegram failure is logged and never raised:
    the profile text is cosmetic and must not break a tick.
    """
    if monthly is None:
        async with session_scope() as session:
            monthly = await repo.monthly_active(session)
    text = profile_text(monthly)
    for language in LANGUAGES:
        try:
            await bot.set_my_description(description=text, language_code=language)
        except Exception as exc:
            log.warning(
                "event=description_failed",
                language=language or "default",
                error=str(exc),
            )
        try:
            await bot.set_my_short_description(
                short_description=SHORT_DESCRIPTION, language_code=language
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
            log.info("event=description_updated", under_name=text, row=SHORT_DESCRIPTION)
        except Exception as exc:
            # A background tick must never kill the bot.
            log.warning("event=description_tick_failed", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue
