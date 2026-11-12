from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("smartbin.description")

UPDATE_INTERVAL = 3600  # seconds between profile refreshes

# The long description is the line under the bot name; the short description
# is the "(i) Description" row and the public t.me share preview.
# Both are fixed brand text — never user counts.
LANGUAGES: tuple[str | None, ...] = (None, "en")
LONG_DESCRIPTION = "CCs🔪🔪🔪🔥"
SHORT_DESCRIPTION = "CC Checker & Killer 🔪"


async def update_description(bot: Any, runtime: Any = None) -> str:
    """Keep the bot's profile text fixed (no counts, no dynamic text)."""
    for language in LANGUAGES:
        kwargs: dict[str, Any] = {}
        if language:
            kwargs["language_code"] = language
        try:
            await bot.set_my_description(
                description=LONG_DESCRIPTION, **kwargs
            )
        except Exception as exc:
            log.warning(
                "event=description_failed language=%s error=%s", language, exc
            )
        try:
            await bot.set_my_short_description(
                short_description=SHORT_DESCRIPTION, **kwargs
            )
        except Exception as exc:
            log.warning(
                "event=short_description_failed language=%s error=%s", language, exc
            )
    log.info(
        "event=description_updated long=%s short=%s",
        LONG_DESCRIPTION,
        SHORT_DESCRIPTION,
    )
    return LONG_DESCRIPTION


async def description_loop(
    bot: Any, runtime: Any = None, interval: int = UPDATE_INTERVAL
) -> None:
    """Refresh the fixed profile text once at boot and then every `interval`."""
    while True:
        await update_description(bot, runtime)
        await asyncio.sleep(interval)
