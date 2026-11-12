from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config import Config, _stripe_keys
from core.gate import GateManager
from core.stripe_keys import (
    StripeKeyRateLimited,
    StripeKeyRejected,
    StripeKeyRing,
    fingerprint,
)
from models import Card


class StripeKeysConfigTest(unittest.TestCase):
    def test_split_and_dedupe_multiple_keys(self):
        env = {
            "STRIPE_API_KEYS": "sk_one, sk_two\nsk_three;sk_four",
            "STRIPE_API_KEY": "sk_five sk_one",
        }
        keys = _stripe_keys(env)
        self.assertEqual(
            keys,
            ("sk_one", "sk_two", "sk_three", "sk_four", "sk_five"),
        )

    def test_twelve_keys_load_together(self):
        env = {"STRIPE_API_KEYS": ",".join(f"sk_test_{i:02d}" for i in range(12))}
        self.assertEqual(len(_stripe_keys(env)), 12)

    def test_legacy_single_key_still_builds_a_ring(self):
        manager = GateManager(Config(stripe_api_key="sk_legacy", stripe_test_mode=False))
        self.assertEqual(len(manager.stripe_keys), 1)

    def test_config_load_reads_lists(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "BOT_TOKEN=1:x\n"
                "STRIPE_API_KEYS=sk_a,sk_b,sk_c\n"
                "STRIPE_API_KEY=sk_a\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                config = Config.load(str(path))
        self.assertEqual(config.stripe_api_keys, ("sk_a", "sk_b", "sk_c"))
        self.assertEqual(config.stripe_api_key, "sk_a")


class StripeKeyRingTest(unittest.TestCase):
    def test_round_robin_and_rejected_cooldown(self):
        ring = StripeKeyRing(["a", "b", "c"], rejected_cooldown=10.0)
        self.assertEqual(ring.candidates(now=0.0), ["a", "b", "c"])
        ring.mark_rejected("a", now=0.0)
        self.assertEqual(ring.candidates(now=1.0), ["b", "c"])
        # Still cooling: skipped entirely.
        self.assertEqual(ring.candidates(now=5.0), ["c", "b"])
        self.assertEqual(ring.candidates(now=11.0), ["a", "b", "c"])

    def test_backoff_grows_after_repeated_rejection(self):
        ring = StripeKeyRing(["a"], rejected_cooldown=10.0, max_cooldown=25.0)
        ring.mark_rejected("a", now=0.0)
        self.assertEqual(ring.candidates(now=10.0), ["a"])
        ring.mark_rejected("a", now=10.0)
        self.assertEqual(ring.candidates(now=20.0), [])  # 20s cooldown
        self.assertEqual(ring.candidates(now=30.0), ["a"])
        ring.mark_rejected("a", now=30.0)
        self.assertEqual(ring.candidates(now=54.0), [])  # capped at 25s
        self.assertEqual(ring.candidates(now=56.0), ["a"])

    def test_rate_limit_uses_short_cooldown_and_healthy_resets(self):
        ring = StripeKeyRing(["a", "b"], rate_limit_cooldown=5.0)
        ring.mark_rate_limited("a", now=0.0)
        self.assertEqual(ring.candidates(now=1.0), ["b"])
        self.assertEqual(ring.candidates(now=6.0), ["b", "a"])
        ring.mark_rejected("b", now=6.0)
        ring.mark_healthy("b")
        self.assertEqual(ring.candidates(now=7.0), ["a", "b"])

    def test_snapshot_masks_keys(self):
        ring = StripeKeyRing(["sk_live_1234567890abcdef"])
        row = ring.snapshot(now=0.0)[0]
        self.assertEqual(row["key"], "sk_live…cdef")
        self.assertNotIn("1234567890", json.dumps(row))


class StripeFailoverTest(unittest.TestCase):
    def _manager(self, *keys: str) -> GateManager:
        config = Config(
            stripe_test_mode=False,
            stripe_api_keys=tuple(keys),
            stripe_api_key=keys[0] if keys else "",
        )
        return GateManager(config)

    def test_rejected_key_rotates_to_next(self):
        manager = self._manager("sk_bad", "sk_good")
        calls: list[str] = []

        async def fake_charge(key, card, amount_minor, proxy):
            calls.append(key)
            if key == "sk_bad":
                raise StripeKeyRejected("Invalid API Key provided", "invalid_api_key")
            return {"approved": True, "status": "succeeded"}

        manager._stripe_charge = fake_charge
        result = asyncio.run(
            manager._stripe(Card.parse("4242424242424242|12|2030|123"), 50, None)
        )
        self.assertTrue(result.get("approved"))
        self.assertEqual(calls, ["sk_bad", "sk_good"])
        self.assertFalse(manager.stripe_keys.snapshot()[0]["ready"])
        self.assertTrue(manager.stripe_keys.snapshot()[1]["ready"])

    def test_card_decline_does_not_rotate(self):
        manager = self._manager("sk_one", "sk_two")
        calls: list[str] = []

        async def fake_charge(key, card, amount_minor, proxy):
            calls.append(key)
            return {"decline_code": "generic_decline", "status": "requires_payment_method"}

        manager._stripe_charge = fake_charge
        result = asyncio.run(
            manager._stripe(Card.parse("4242424242424242|12|2030|123"), 50, None)
        )
        self.assertEqual(result["decline_code"], "generic_decline")
        self.assertEqual(calls, ["sk_one"])

    def test_all_keys_cooling_fails_fast(self):
        manager = self._manager("sk_bad", "sk_also_bad")
        calls: list[str] = []

        async def fake_charge(key, card, amount_minor, proxy):
            calls.append(key)
            raise StripeKeyRejected("revoked", "api_key_expired")

        manager._stripe_charge = fake_charge
        card = Card.parse("4242424242424242|12|2030|123")
        first = asyncio.run(manager._stripe(card, 50, None))
        self.assertEqual(len(calls), 2)
        self.assertIn("error", first)
        second = asyncio.run(manager._stripe(card, 50, None))
        self.assertEqual(len(calls), 2)  # no extra requests while cooling
        self.assertIn("cooling down", second["error"])

    def test_missing_keys_still_reports_clearly(self):
        manager = GateManager(Config(stripe_test_mode=False))
        result = asyncio.run(
            manager._stripe(Card.parse("4242424242424242|12|2030|123"), 50, None)
        )
        self.assertIn("STRIPE_API_KEY missing", result["error"])


class _Response:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


class _Client:
    def __init__(self, *responses: _Response):
        self.responses = list(responses)

    async def post(self, *args, **kwargs):
        return self.responses.pop(0)


class StripePostTest(unittest.TestCase):
    def _manager(self) -> GateManager:
        return GateManager(
            Config(
                stripe_test_mode=False,
                stripe_api_keys=("sk_a", "sk_b"),
                stripe_api_key="sk_a",
            )
        )

    def test_auth_status_raises_rejected(self):
        client = _Client(_Response(401, {"error": {"message": "Invalid API Key"}}))
        with self.assertRaises(StripeKeyRejected):
            asyncio.run(
                self._manager()._stripe_post(
                    client, "https://api.stripe.com/v1/payment_methods", ("sk_a", ""), {}
                )
            )

    def test_rate_limit_raises_immediately_with_pool(self):
        client = _Client(_Response(429, {"error": {"message": "rate limited"}}))
        with self.assertRaises(StripeKeyRateLimited):
            asyncio.run(
                self._manager()._stripe_post(
                    client, "https://api.stripe.com/v1/payment_methods", ("sk_a", ""), {}
                )
            )

    def test_card_error_is_returned_not_rotated(self):
        client = _Client(
            _Response(
                400,
                {
                    "error": {
                        "message": "Your card number is incorrect.",
                        "code": "incorrect_number",
                        "decline_code": "",
                    }
                },
            )
        )
        result = asyncio.run(
            self._manager()._stripe_post(
                client, "https://api.stripe.com/v1/payment_methods", ("sk_a", ""), {}
            )
        )
        self.assertIn("incorrect", result["error"])
        self.assertEqual(result["raw_code"], "incorrect_number")


if __name__ == "__main__":
    unittest.main()
