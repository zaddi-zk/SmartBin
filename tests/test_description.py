"""Bot profile: the count goes under the name, the tagline stays in the row."""

from __future__ import annotations

from typing import Any

from app.tg.description import SHORT_DESCRIPTION, profile_text, update_description


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


async def test_profile_text_matches_the_legacy_wording() -> None:
    assert profile_text(19) == "19 monthly users"


async def test_count_goes_under_the_name_and_tagline_in_the_row() -> None:
    bot = FakeBot()
    text = await update_description(bot, monthly=19)

    assert text == "19 monthly users"
    # Long description = under the bot name.
    assert bot.long == [(None, "19 monthly users"), ("en", "19 monthly users")]
    # Short description = the "(i) Description" row.
    assert bot.short == [
        (None, "CC Checker & Killer 🔪"),
        ("en", "CC Checker & Killer 🔪"),
    ]
    assert SHORT_DESCRIPTION == "CC Checker & Killer 🔪"


async def test_a_telegram_failure_does_not_raise() -> None:
    class BadBot:
        async def set_my_description(self, **kwargs: Any) -> None:
            raise RuntimeError("telegram down")

        async def set_my_short_description(self, **kwargs: Any) -> None:
            raise RuntimeError("telegram down")

    assert await update_description(BadBot(), monthly=1) == "1 monthly users"
