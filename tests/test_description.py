"""Bot profile: only the two API-managed fields, at fixed brand text.

The line under the bot name is the About text, which the Bot API cannot touch.
These tests pin the two fields it *can* write so a stale count can never end up
in either of them again.
"""

from __future__ import annotations

from typing import Any

from app.tg.description import (
    LONG_DESCRIPTION,
    SHORT_DESCRIPTION,
    update_description,
)


class FakeBot:
    def __init__(self) -> None:
        self.long: list[tuple[str | None, str]] = []
        self.short: list[tuple[str | None, str]] = []

    async def set_my_description(
        self, description: str = "", language_code: str | None = None
    ) -> None:
        self.long.append((language_code, description))

    async def set_my_short_description(
        self, short_description: str = "", language_code: str | None = None
    ) -> None:
        self.short.append((language_code, short_description))


def test_defaults_are_brand_text_without_a_count() -> None:
    assert LONG_DESCRIPTION == "CCs🔪🔪🔪🔥"
    assert SHORT_DESCRIPTION == "CC Checker & Killer 🔪"
    assert "monthly" not in LONG_DESCRIPTION.lower()
    assert "monthly" not in SHORT_DESCRIPTION.lower()


async def test_writes_both_fields_in_both_languages() -> None:
    bot = FakeBot()
    text = await update_description(bot)

    assert text == LONG_DESCRIPTION
    assert bot.long == [(None, LONG_DESCRIPTION), ("en", LONG_DESCRIPTION)]
    assert bot.short == [(None, SHORT_DESCRIPTION), ("en", SHORT_DESCRIPTION)]


async def test_a_telegram_failure_does_not_raise() -> None:
    class BadBot:
        async def set_my_description(self, **kwargs: Any) -> None:
            raise RuntimeError("telegram down")

        async def set_my_short_description(self, **kwargs: Any) -> None:
            raise RuntimeError("telegram down")

    assert await update_description(BadBot()) == LONG_DESCRIPTION
