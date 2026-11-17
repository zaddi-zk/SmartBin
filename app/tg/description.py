"""Bot profile text.

Telegram exposes two profile fields to the Bot API:

* ``setMyDescription`` — "What can this bot do?" in an empty chat
* ``setMyShortDescription`` — the "(i) Description" row and the t.me preview

The line **under the bot name** is the bot's *About text*. It is not exposed by
the Bot API; only @BotFather's ``/setabouttext`` changes it. This module keeps
the two API fields at the brand text that was there before, so it never fights
the About line or leaves a stale count in the wrong place.
"""

from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot

log = structlog.get_logger("tg.description")

UPDATE_INTERVAL = 3600  # seconds between profile refreshes
LANGUAGES: tuple[str | None, ...] = (None, "en")

# "What can this bot do?" in an empty chat.
LONG_DESCRIPTION = "CCs🔪🔪🔪🔥"
# The "(i) Description" row and the public t.me share preview.
SHORT_DESCRIPTION = "CC Checker & Killer 🔪"


async def update_description(bot: Bot) -> str:
    """Restore the two API-managed profile fields. Returns the long text.

    A Telegram failure is logged and never raised: cosmetic text must not
    break a tick.
    """
    for language in LANGUAGES:
        try:
            await bot.set_my_description(
                description=LONG_DESCRIPTION, language_code=language
            )
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
    return LONG_DESCRIPTION


async def description_tick(
    bot: Bot, stop: asyncio.Event, interval_s: int = UPDATE_INTERVAL
) -> None:
    while not stop.is_set():
        try:
            text = await update_description(bot)
            log.info("event=description_updated", long=text, short=SHORT_DESCRIPTION)
        except Exception as exc:
            # A background tick must never kill the bot.
            log.warning("event=description_tick_failed", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue
