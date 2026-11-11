from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

from db import DB, today_utc
from quota import claim_usage, quota_snapshot, remaining, refund_usage


def make_config(**overrides):
    base = SimpleNamespace(
        admin_id=0,
        free_bins_per_day=3,
        free_checks_per_day=2,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


class QuotaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = await DB(self.path).init()
        self.config = make_config()

    async def asyncTearDown(self):
        await self.db.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass

    async def _user(self, uid: int = 1, **fields):
        await self.db.ensure_user(uid)
        if fields:
            await self.db.update_user(uid, **fields)
        return await self.db.reset_quota_if_needed(uid)

    async def test_three_bins_then_paywall(self):
        user = await self._user()
        results = [
            await claim_usage(self.db, user, "bin", self.config) for _ in range(4)
        ]
        self.assertEqual([r["allowed"] for r in results], [True, True, True, False])
        self.assertTrue(results[3]["limit_reached"])
        self.assertEqual(
            int(await self.db.scalar("SELECT free_bin FROM users WHERE user_id=1")), 3
        )

    async def test_two_checks_then_credits_then_paywall(self):
        user = await self._user(credits=1)
        outcomes = [
            await claim_usage(self.db, user, "check", self.config) for _ in range(3)
        ]
        self.assertEqual([o["consume"] for o in outcomes], ["free", "free", "credit"])
        fourth = await claim_usage(self.db, user, "check", self.config)
        self.assertFalse(fourth["allowed"])
        self.assertEqual(
            int(await self.db.scalar("SELECT credits FROM users WHERE user_id=1")), 0
        )

    async def test_bin_and_check_counters_are_independent(self):
        user = await self._user()
        for _ in range(3):
            self.assertTrue((await claim_usage(self.db, user, "bin", self.config))["allowed"])
        self.assertFalse((await claim_usage(self.db, user, "bin", self.config))["allowed"])
        self.assertTrue((await claim_usage(self.db, user, "check", self.config))["allowed"])

    async def test_premium_and_admin_are_unlimited(self):
        premium = await self._user(2, premium_until="2099-01-01T00:00:00Z")
        for _ in range(10):
            decision = await claim_usage(self.db, premium, "bin", self.config)
            self.assertTrue(decision["allowed"])
            self.assertEqual(decision["consume"], "premium")
        self.assertEqual(
            int(await self.db.scalar("SELECT free_bin FROM users WHERE user_id=2")), 0
        )

        admin_cfg = make_config(admin_id=3)
        await self.db.ensure_user(3)
        admin = await self.db.reset_quota_if_needed(3)
        self.assertTrue((await claim_usage(self.db, admin, "check", admin_cfg))["allowed"])

    async def test_banned_is_refused(self):
        user = await self._user(4, is_banned=1)
        decision = await claim_usage(self.db, user, "bin", self.config)
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["consume"], "banned")

    async def test_concurrent_taps_cannot_exceed_limit(self):
        user = await self._user()
        claims = await asyncio.gather(
            *(claim_usage(self.db, user, "bin", self.config) for _ in range(25))
        )
        allowed = sum(1 for c in claims if c["allowed"])
        self.assertEqual(allowed, 3, f"over-granted: {allowed} of 25")
        self.assertEqual(
            int(await self.db.scalar("SELECT free_bin FROM users WHERE user_id=1")), 3
        )

    async def test_concurrent_credit_spend_never_goes_negative(self):
        user = await self._user(credits=2)
        await self.db.update_user(1, free_check=2)  # free allowance already used
        user = await self.db.get_user(1)
        claims = await asyncio.gather(
            *(claim_usage(self.db, user, "check", self.config) for _ in range(10))
        )
        self.assertEqual(sum(1 for c in claims if c["allowed"]), 2)
        self.assertEqual(
            int(await self.db.scalar("SELECT credits FROM users WHERE user_id=1")), 0
        )

    async def test_bonus_checks_extend_the_daily_allowance(self):
        await self._user()
        await self.db.grant_referral_bonus(1, 1)  # +5 bonus (only one grant per referee)
        user = await self.db.reset_quota_if_needed(1)
        self.assertEqual(int(user["bonus_check"]), 5)
        outcomes = [
            await claim_usage(self.db, user, "check", self.config) for _ in range(3)
        ]
        self.assertTrue(all(o["allowed"] for o in outcomes))
        self.assertEqual([o["consume"] for o in outcomes], ["free", "free", "bonus"])
        # Bonus is a finite balance, not a permanent higher daily limit.
        self.assertEqual(
            int(await self.db.scalar("SELECT bonus_check FROM users WHERE user_id=1")), 4
        )

    async def test_referral_replay_grants_nothing(self):
        await self._user()
        self.assertTrue(await self.db.grant_referral_bonus(1, 1))
        self.assertFalse(await self.db.grant_referral_bonus(1, 1))
        self.assertEqual(
            int(await self.db.scalar("SELECT bonus_check FROM users WHERE user_id=1")), 5
        )

    async def test_bonus_checks_run_out(self):
        await self._user()
        await self.db.grant_referral_bonus(1, 1)  # 5 bonus
        user = await self.db.reset_quota_if_needed(1)
        allowed = 0
        for _ in range(20):
            if (await claim_usage(self.db, user, "check", self.config))["allowed"]:
                allowed += 1
        self.assertEqual(allowed, 7)  # 2 daily + 5 bonus, then paywall
        self.assertEqual(
            int(await self.db.scalar("SELECT bonus_check FROM users WHERE user_id=1")), 0
        )
        self.assertEqual(
            int(await self.db.scalar("SELECT free_check FROM users WHERE user_id=1")), 2
        )

    async def test_refund_returns_the_unit(self):
        user = await self._user(credits=1)
        decision = await claim_usage(self.db, user, "check", self.config)
        self.assertEqual(decision["consume"], "free")
        await refund_usage(self.db, 1, "check", decision["consume"])
        user = await self.db.get_user(1)
        self.assertEqual(int(user["free_check"]), 0)
        self.assertEqual(remaining(user, "check", self.config), 3)

    async def test_utc_rollover_resets_only_daily_counters(self):
        user = await self._user(credits=7, bonus_check=4)
        for _ in range(3):
            await claim_usage(self.db, user, "bin", self.config)
        await self.db.update_user(1, quota_date="2000-01-01")
        user = await self.db.reset_quota_if_needed(1)
        self.assertEqual(user["quota_date"], today_utc())
        self.assertEqual(int(user["free_bin"]), 0)
        self.assertEqual(int(user["free_check"]), 0)
        self.assertEqual(int(user["credits"]), 7)
        self.assertEqual(int(user["bonus_check"]), 4)

    async def test_daily_dedupe_is_per_user_per_day(self):
        self.assertTrue(await self.db.mark_daily_usage(1, "bin", "45717360"))
        self.assertFalse(await self.db.mark_daily_usage(1, "bin", "45717360"))
        self.assertTrue(await self.db.mark_daily_usage(2, "bin", "45717360"))
        self.assertTrue(await self.db.mark_daily_usage(1, "bin", "42424242"))
        await self.db.unmark_daily_usage(1, "bin", "45717360")
        self.assertTrue(await self.db.mark_daily_usage(1, "bin", "45717360"))

    async def test_snapshot_matches_consumption(self):
        user = await self._user()
        await claim_usage(self.db, user, "bin", self.config)
        await claim_usage(self.db, user, "check", self.config)
        user = await self.db.get_user(1)
        snap = quota_snapshot(user, self.config)
        self.assertEqual(snap["free_bins_used"], 1)
        self.assertEqual(snap["free_bins_left"], 2)
        self.assertEqual(snap["free_checks_used"], 1)
        self.assertEqual(snap["free_checks_left"], 1)
        self.assertFalse(snap["unlimited"])


if __name__ == "__main__":
    unittest.main()
