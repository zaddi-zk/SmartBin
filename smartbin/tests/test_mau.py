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


if __name__ == "__main__":
    unittest.main()
