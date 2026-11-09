from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

log = logging.getLogger("smartbin.description")

UPDATE_INTERVAL = 3600  # seconds between profile refreshes

# Short description is the text shown directly under the bot's name.
# Telegram serves a language-specific value when one exists, so set the
# default and English explicitly (the profile UI in the screenshots is English).
SHORT_DESCRIPTION_LANGUAGES: tuple[str | None, ...] = (None, "en")

_MONTHLY_TEXT = re.compile(r"^\s*[\d,]+ monthly users\s*$")


async def update_description(bot: Any, runtime: Any) -> str | None:
    """Publish the monthly-active-user count as the bot's short description."""
    try:
        count = await runtime.db.monthly_users()
    except Exception as exc:
        log.warning("event=description_count_failed error=%s", exc)
        return None

    text = f"{count:,} monthly users"
    for language in SHORT_DESCRIPTION_LANGUAGES:
        kwargs: dict[str, Any] = {"short_description": text}
        if language:
            kwargs["language_code"] = language
        try:
            await bot.set_my_short_description(**kwargs)
        except Exception as exc:
            log.warning(
                "event=short_description_failed language=%s error=%s", language, exc
            )
    log.info("event=description_updated text=%s", text)
    return text


async def clear_legacy_description(bot: Any) -> None:
    """Remove the count from the long description if an older build put it there."""
    for language in SHORT_DESCRIPTION_LANGUAGES:
        kwargs: dict[str, Any] = {}
        if language:
            kwargs["language_code"] = language
        try:
            current = await bot.get_my_description(**kwargs)
        except Exception as exc:
            log.warning(
                "event=get_description_failed language=%s error=%s", language, exc
            )
            continue
        text = getattr(current, "description", "") or ""
        if not _MONTHLY_TEXT.match(text):
            continue
        try:
            await bot.set_my_description(description="", **kwargs)
        except Exception as exc:
            log.warning(
                "event=clear_description_failed language=%s error=%s", language, exc
            )


async def description_loop(
    bot: Any, runtime: Any, interval: int = UPDATE_INTERVAL
) -> None:
    """Refresh the profile count once at boot and then every `interval` seconds."""
    await clear_legacy_description(bot)
    while True:
        await update_description(bot, runtime)
        await asyncio.sleep(interval)
