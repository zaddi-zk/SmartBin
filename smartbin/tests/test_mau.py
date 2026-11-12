from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import DB


class _FakeBot:
    def __init__(self):
        self.short: dict[str | None, str] = {}
        self.long: dict[str | None, str] = {}

    async def set_my_short_description(self, short_description="", language_code=None):
        self.short[language_code] = short_description

    async def set_my_description(self, description="", language_code=None):
        self.long[language_code] = description


class _FakeDB:
    def __init__(self, count: int | Exception):
        self._count = count

    async def user_count(self) -> int:
        if isinstance(self._count, Exception):
            raise self._count
        return self._count


class _FakeRuntime:
    def __init__(self, count: int | Exception):
        self.db = _FakeDB(count)


class MonthlyUsersTest(unittest.TestCase):
    def test_monthly_active_count(self):
        asyncio.run(self._run())

    async def _run(self):
        tmp = Path(tempfile.mkdtemp(prefix="smartbin-mau-", dir="/tmp/opencode"))
        db = await DB(str(tmp / "mau.db")).init()
        try:
            await db.ensure_user(1, "a", "A")
            await db.ensure_user(2, "b", "B")
            self.assertEqual(await db.monthly_users(), 2)

            old = (datetime.now(timezone.utc) - timedelta(days=45)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            await db.update_user(2, last_seen_utc=old)
            self.assertEqual(await db.monthly_users(), 1)

            # Any new interaction makes the user active again.
            await db.ensure_user(2, "b", "B")
            self.assertEqual(await db.monthly_users(), 2)

            # Rows from before activity tracking have no last_seen: count them
            # from joined_utc instead of silently dropping them.
            await db.ensure_user(3, "c", "C")
            await db.update_user(3, last_seen_utc="")
            self.assertEqual(await db.monthly_users(), 3)
        finally:
            await db.close()

    def test_touch_user_updates_timestamp_with_hourly_throttle(self):
        asyncio.run(self._run_touch())

    async def _run_touch(self):
        tmp = Path(tempfile.mkdtemp(prefix="smartbin-touch-", dir="/tmp/opencode"))
        db = await DB(str(tmp / "touch.db")).init()
        now = datetime.now(timezone.utc)

        def stamp(delta: timedelta) -> str:
            return (now + delta).strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            await db.ensure_user(1, "a", "A")

            # Recent activity is throttled: no write within the last hour.
            recent = stamp(timedelta(minutes=-10))
            await db.update_user(1, last_seen_utc=recent)
            await db.touch_user(1, "a", "A")
            row = await db.fetchone("SELECT last_seen_utc FROM users WHERE user_id=1")
            self.assertEqual(row["last_seen_utc"], recent)

            # Stale activity is refreshed on the next interaction.
            stale = stamp(timedelta(hours=-2))
            await db.update_user(1, last_seen_utc=stale)
            await db.touch_user(1, "a", "A")
            row = await db.fetchone("SELECT last_seen_utc FROM users WHERE user_id=1")
            self.assertGreater(row["last_seen_utc"], stale)

            # Unknown users are created on first interaction.
            await db.touch_user(99, "new", "New")
            self.assertIsNotNone(
                await db.fetchone("SELECT user_id FROM users WHERE user_id=99")
            )
            self.assertEqual(await db.monthly_users(), 2)
        finally:
            await db.close()

    def test_monthly_count_is_not_sent_as_chat_text(self):
        from types import SimpleNamespace

        import render
        import ui

        config = SimpleNamespace(display_version="7.1.0", premium_emoji_id="")
        menu = ui.menu_text(config, None)
        self.assertNotIn("monthly users", menu)

        block = render.status_block(
            {
                "uptime": "1h",
                "users": 3,
                "premium": 0,
                "monthly_users": 5746,
                "checks_today": 0,
                "cache_depth": 0,
                "p50": 0,
                "p95": 0,
                "gates": [],
            }
        )
        self.assertNotIn("monthly users", block)
        self.assertNotIn("USERS", block)
        self.assertNotIn("PREMIUM", block)

    def test_description_task_updates_bot_profile(self):
        asyncio.run(self._run_description())

    async def _run_description(self):
        from description import SHORT_DESCRIPTION, update_description

        bot = _FakeBot()
        runtime = _FakeRuntime(26)
        text = await update_description(bot, runtime)
        self.assertEqual(text, "26 users")
        # Count goes under the bot name in every language variant.
        self.assertEqual(bot.long[None], "26 users")
        self.assertEqual(bot.long["en"], "26 users")
        self.assertEqual(bot.short[None], SHORT_DESCRIPTION)
        self.assertEqual(bot.short["en"], SHORT_DESCRIPTION)

    def test_description_is_skipped_when_count_fails(self):
        asyncio.run(self._run_description_failure())

    async def _run_description_failure(self):
        from description import update_description

        bot = _FakeBot()
        text = await update_description(bot, _FakeRuntime(RuntimeError("db down")))
        self.assertIsNone(text)
        # A DB hiccup must not overwrite the live profile text.
        self.assertEqual(bot.long, {})
        self.assertEqual(bot.short, {})


if __name__ == "__main__":
    unittest.main()
