from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode

from aiohttp.test_utils import TestClient, TestServer

from db import DB
from miniapp.server import create_app
from models import CheckOutcome

TOKEN = "123456789:TESTTOKEN_TESTTOKEN_TESTTOKEN_TEST"
UID = 555001


def sign(init_pairs: dict, token: str = TOKEN) -> str:
    check = "\n".join(f"{k}={init_pairs[k]}" for k in sorted(init_pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs = dict(init_pairs)
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def init_data(user_id: int = UID) -> str:
    user = json.dumps({"id": user_id, "first_name": "Quota", "username": "quota"})
    return sign({"auth_date": str(int(time.time())), "query_id": "AAHq", "user": user})


class FakeStore:
    async def get(self, bin8: str):
        return self._record(bin8)

    async def get_prefix(self, prefix: str):
        return self._record(prefix)

    @staticmethod
    def _record(bin8: str):
        return SimpleNamespace(
            bin=bin8,
            brand="VISA",
            level="CLASSIC",
            bank="TEST BANK",
            country="United States",
            iso2="US",
            currency="USD",
            phone="",
            site="",
            prepaid=0,
            commercial=0,
            avs=1,
            card_length=16,
            luhn=1,
            bin_age=5,
            sample_size=10,
            verified_utc="2026-01-01T00:00:00Z",
            confidence=90.0,
            sources_json="{}",
            field_conf_json="{}",
            twofa="UNKNOWN",
            twofa_conf=0.0,
            twofa_reads=0,
        )


class FakeRunner:
    def __init__(self, outcome=None):
        self.calls = 0
        self.outcome = outcome

    async def run(self, user_id: int, raw: str, purpose: str = "check"):
        self.calls += 1
        if self.outcome is not None:
            return self.outcome
        # Use the real outcome type: a wrong attribute here must fail the test,
        # not burn a production quota unit and return HTTP 500.
        return CheckOutcome(
            verdict="APPROVED", live=1, action="approved", latency_ms=10, note="ok"
        )


CARD = "4242424242424242|12|2030|123"


class MiniAppQuotaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = await DB(self.path).init()
        self.config = SimpleNamespace(
            bot_token=TOKEN,
            admin_id=0,
            free_bins_per_day=3,
            free_checks_per_day=2,
        )
        self.runtime = SimpleNamespace(
            config=self.config,
            db=self.db,
            store=FakeStore(),
            runner=FakeRunner(),
            uptime=lambda: "0d 0h 0m",
        )
        self.client = TestClient(TestServer(create_app(self.runtime)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.db.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass

    async def _bin(self, signed: bool = True):
        headers = {"X-Telegram-Init-Data": init_data()} if signed else {}
        async with self.client.get("/api/bin?bin=45717360", headers=headers) as resp:
            body = await resp.json()
            return resp.status, body

    async def test_unsigned_is_rejected(self):
        status, body = await self._bin(signed=False)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")

    async def test_three_free_lookups_then_402(self):
        for expected_left in (2, 1, 0):
            status, body = await self._bin()
            self.assertEqual(status, 200, body)
            self.assertEqual(body["quota"]["free_bins_left"], expected_left)

        status, body = await self._bin()
        self.assertEqual(status, 402, body)
        self.assertEqual(body["code"], "limit_reached")
        self.assertEqual(body["kind"], "bin")
        self.assertEqual(body["quota"]["free_bins_left"], 0)

    async def test_credit_unlocks_one_more_lookup(self):
        for _ in range(3):
            self.assertEqual((await self._bin())[0], 200)
        self.assertEqual((await self._bin())[0], 402)

        await self.db.user_grant(UID, 2)
        status, body = await self._bin()
        self.assertEqual(status, 200, body)
        self.assertEqual(body["quota"]["credits"], 1)

        status, body = await self._bin()
        self.assertEqual(status, 200, body)
        self.assertEqual(body["quota"]["credits"], 0)

        self.assertEqual((await self._bin())[0], 402)

    async def test_miniapp_status_exposes_quota(self):
        headers = {"X-Telegram-Init-Data": init_data()}
        async with self.client.get("/api/status", headers=headers) as resp:
            self.assertEqual(resp.status, 200)
            body = await resp.json()
        self.assertIn("quota", body)
        self.assertEqual(body["quota"]["free_bins_limit"], 3)
        self.assertEqual(body["quota"]["free_checks_limit"], 2)
        self.assertEqual(body["user"]["id"], UID)

    async def test_premium_bypasses_daily_quota(self):
        await self.db.ensure_user(UID)
        await self.db.update_user(UID, premium_until="2099-01-01T00:00:00Z")
        for _ in range(6):
            status, body = await self._bin()
            self.assertEqual(status, 200, body)
            self.assertEqual(body["quota"]["free_bins_used"], 0)
            self.assertTrue(body["quota"]["unlimited"])

    async def _check(self):
        headers = {
            "X-Telegram-Init-Data": init_data(),
            "Content-Type": "application/json",
        }
        async with self.client.post("/api/check", json={"card": CARD}, headers=headers) as resp:
            body = await resp.json()
            return resp.status, body

    async def test_two_free_checks_then_402(self):
        for expected_left in (1, 0):
            status, body = await self._check()
            self.assertEqual(status, 200, body)
            self.assertEqual(body["verdict"], "APPROVED")
            self.assertIn("message", body)
            self.assertEqual(body["quota"]["free_checks_left"], expected_left)

        status, body = await self._check()
        self.assertEqual(status, 402, body)
        self.assertEqual(body["code"], "limit_reached")
        self.assertEqual(body["kind"], "check")
        self.assertEqual(self.runtime.runner.calls, 2)  # refused check never ran

    async def test_credit_unlocks_one_more_check(self):
        for _ in range(2):
            self.assertEqual((await self._check())[0], 200)
        self.assertEqual((await self._check())[0], 402)

        await self.db.user_grant(UID, 1)
        status, body = await self._check()
        self.assertEqual(status, 200, body)
        self.assertEqual(body["quota"]["credits"], 0)

        self.assertEqual((await self._check())[0], 402)

    async def test_undispatched_check_is_not_charged(self):
        self.runtime.runner = FakeRunner(
            CheckOutcome(
                verdict="INCONCLUSIVE", action="core-degraded", note="no healthy gate"
            )
        )
        for _ in range(4):
            status, body = await self._check()
            self.assertEqual(status, 200, body)
            self.assertEqual(body["quota"]["free_checks_left"], 2)
            self.assertEqual(body["quota"]["free_checks_used"], 0)
        self.assertEqual(self.runtime.runner.calls, 4)


if __name__ == "__main__":
    unittest.main()
