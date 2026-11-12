from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("smartbin.description")

UPDATE_INTERVAL = 1800  # seconds between profile refreshes

# The long description is the line under the bot name; the short description
# is the "(i) Description" row and the public t.me share preview.
LANGUAGES: tuple[str | None, ...] = (None, "en")
SHORT_DESCRIPTION = "CC Checker & Killer 🔪"


def profile_text(count: int) -> str:
    """Line under the bot name: live count of people using the bot."""
    return f"{count:,} monthly users"


async def _monthly_users(runtime: Any) -> int | None:
    if runtime is None:
        return None
    try:
        return int(await runtime.db.monthly_users())
    except Exception as exc:
        log.warning("event=description_count_failed error=%s", exc)
        return None


async def update_description(bot: Any, runtime: Any = None) -> str | None:
    """Publish the live user count under the bot name and keep the tagline."""
    count = await _monthly_users(runtime)
    if count is None:
        # Never replace a good count with a guess when the DB hiccups.
        return None

    text = profile_text(count)
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
            await bot.set_my_short_description(
                short_description=SHORT_DESCRIPTION, **kwargs
            )
        except Exception as exc:
            log.warning(
                "event=short_description_failed language=%s error=%s", language, exc
            )
    log.info("event=description_updated text=%s", text)
    return text


async def description_loop(
    bot: Any, runtime: Any = None, interval: int = UPDATE_INTERVAL
) -> None:
    """Refresh the profile count once at boot and then every `interval` seconds."""
    while True:
        await update_description(bot, runtime)
        await asyncio.sleep(interval)
