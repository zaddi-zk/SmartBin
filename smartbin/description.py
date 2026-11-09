from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("smartbin.description")

UPDATE_INTERVAL = 3600  # seconds between profile refreshes


async def update_description(bot: Any, runtime: Any) -> str | None:
    """Publish the monthly-active-user count in the bot profile.

    Telegram exposes two profile fields; we set both so the number appears
    under the bot name (short description) and on an empty chat (description).
    """
    try:
        count = await runtime.db.monthly_users()
    except Exception as exc:
        log.warning("event=description_count_failed error=%s", exc)
        return None

    text = f"{count:,} monthly users"
    try:
        await bot.set_my_short_description(short_description=text)
    except Exception as exc:
        log.warning("event=short_description_failed error=%s", exc)
    try:
        await bot.set_my_description(description=text)
    except Exception as exc:
        log.warning("event=description_failed error=%s", exc)
    log.info("event=description_updated text=%s", text)
    return text


async def description_loop(
    bot: Any, runtime: Any, interval: int = UPDATE_INTERVAL
) -> None:
    """Refresh the profile count once at boot and then every `interval` seconds."""
    while True:
        await update_description(bot, runtime)
        await asyncio.sleep(interval)
