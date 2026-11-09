from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import DB


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

    def test_menu_and_public_status_show_monthly_users_only(self):
        from types import SimpleNamespace

        import render
        import ui

        config = SimpleNamespace(display_version="7.1.0", premium_emoji_id="")
        menu = ui.menu_text(config, None, 5746)
        self.assertIn("📊  <b>5,746 monthly users</b>", menu)

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
        self.assertIn("📊  5,746 monthly users", block)
        self.assertNotIn("USERS", block)
        self.assertNotIn("PREMIUM", block)


if __name__ == "__main__":
    unittest.main()
