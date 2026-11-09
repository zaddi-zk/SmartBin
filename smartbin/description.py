from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("smartbin.description")

UPDATE_INTERVAL = 3600  # seconds between profile refreshes

# Telegram clients show the *long* description directly under the bot name
# in the Bot Info popup, while the *short* description fills the
# "(i) Description" row and the public t.me share preview. Card Venom uses
# exactly this layout (its t.me preview is the marketing text, not the count).
LANGUAGES: tuple[str | None, ...] = (None, "en")
SHORT_DESCRIPTION = (
    "Card intelligence · cache-first metadata · measured 2FA · live verdicts"
)


async def update_description(bot: Any, runtime: Any) -> str | None:
    """Publish the monthly-active-user count under the bot's name."""
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
            # Long description: shown directly under the bot name.
            await bot.set_my_description(description=text, **kwargs)
        except Exception as exc:
            log.warning(
                "event=description_failed language=%s error=%s", language, exc
            )
        try:
            # Short description: the profile "Description" row / share preview.
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
    bot: Any, runtime: Any, interval: int = UPDATE_INTERVAL
) -> None:
    """Refresh the profile count once at boot and then every `interval` seconds."""
    while True:
        await update_description(bot, runtime)
        await asyncio.sleep(interval)
