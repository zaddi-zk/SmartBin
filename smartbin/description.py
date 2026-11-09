from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("smartbin.description")

UPDATE_INTERVAL = 3600  # seconds between profile refreshes

# Telegram clients differ in which field they render under the bot's name
# (Bot Info popup vs. older clients). Set both fields for the default and
# English variants so the count is guaranteed to appear there.
LANGUAGES: tuple[str | None, ...] = (None, "en")


async def update_description(bot: Any, runtime: Any) -> str | None:
    """Publish the monthly-active-user count in the bot profile."""
    try:
        count = await runtime.db.monthly_users()
    except Exception as exc:
        log.warning("event=description_count_failed error=%s", exc)
        return None

    text = f"{count:,} monthly users"
    for language in LANGUAGES:
        kwargs: dict[str, Any] = {}
        if language:
            kwargs["language_code"] = language
        try:
            await bot.set_my_description(description=text, **kwargs)
        except Exception as exc:
            log.warning(
                "event=description_failed language=%s error=%s", language, exc
            )
        try:
            await bot.set_my_short_description(short_description=text, **kwargs)
        except Exception as exc:
            log.warning(
                "event=short_description_failed language=%s error=%s", language, exc
            )
    log.info("event=description_updated text=%s", text)
    return text


async def description_loop(
    bot: Any, runtime: Any, interval: int = UPDATE_INTERVAL
) -> None:
    """Refresh the profile count once at boot and then every `interval` seconds."""
    while True:
        await update_description(bot, runtime)
        await asyncio.sleep(interval)
