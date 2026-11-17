"""Bot profile: the monthly-user line is measured, never a stale constant."""

from __future__ import annotations

from typing import Any

from app.tg.description import short_description, update_description


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str]] = []

    async def set_my_short_description(
        self, short_description: str = "", language_code: str | None = None
    ) -> None:
        self.calls.append((language_code, short_description))


async def test_short_description_matches_the_legacy_wording() -> None:
    assert short_description(19) == "19 monthly users"


async def test_update_writes_the_default_and_english_profile() -> None:
    bot = FakeBot()
    text = await update_description(bot, monthly=19)

    assert text == "19 monthly users"
    assert bot.calls == [(None, "19 monthly users"), ("en", "19 monthly users")]


async def test_a_telegram_failure_does_not_raise() -> None:
    class BadBot:
        async def set_my_short_description(self, **kwargs: Any) -> None:
            raise RuntimeError("telegram down")

    assert await update_description(BadBot(), monthly=1) == "1 monthly users"
