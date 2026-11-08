from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.reconciler import (  # noqa: E402
    decay,
    field_confidence,
    normalize_level,
    reconcile,
)

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class ReconcilerTests(unittest.TestCase):
    def test_agreement_two_sources_high_confidence(self):
        data = reconcile(
            "45717360",
            [
                ("binlist", 1.0, {"bank": "Jyske Bank", "brand": "Visa", "level": "CLASSIC"}),
                ("antipublic", 0.8, {"bank": "Jyske Bank", "brand": "Visa", "level": "CLASSIC"}),
            ],
            now=NOW,
        )
        self.assertEqual(data["bank"], "Jyske Bank")
        self.assertGreaterEqual(data["field_conf_json"]["bank"], 80.0)
        self.assertGreaterEqual(data["confidence"], 80.0)

    def test_disagreement_penalty(self):
        agree = reconcile(
            "1",
            [("a", 1.0, {"bank": "Chase"}), ("b", 0.8, {"bank": "Chase"})],
            now=NOW,
        )
        disagree = reconcile(
            "1",
            [("a", 1.0, {"bank": "Chase"}), ("b", 0.8, {"bank": "JPMorgan"})],
            now=NOW,
        )
        self.assertLess(
            disagree["field_conf_json"]["bank"], agree["field_conf_json"]["bank"]
        )
        self.assertEqual(field_confidence(1.8, 1.8, 2, 0), 90.0)

    def test_single_source_is_low_confidence(self):
        data = reconcile("1", [("seed", 0.4, {"bank": "Jyske Bank"})], now=NOW)
        self.assertLess(data["field_conf_json"]["bank"], 60.0)

    def test_decay_30_and_90_days(self):
        verified = iso(NOW)
        self.assertEqual(decay(100.0, verified, now=NOW + timedelta(days=30)), 100.0)
        self.assertEqual(decay(100.0, verified, now=NOW + timedelta(days=90)), 50.0)
        self.assertAlmostEqual(decay(100.0, verified, now=NOW + timedelta(days=60)), 75.0, places=1)
        self.assertEqual(decay(100.0, verified, now=NOW + timedelta(days=120)), 50.0)

    def test_level_normalization_table(self):
        cases = {
            "classic": "CLASSIC",
            "world elite": "WORLD",
            "debit": "STANDARD",
            "platinum": "PLATINUM",
            "VISA GOLD": "GOLD",
            "business debit": "BUSINESS",
            "garbage-tier": "UNKNOWN",
            "": "UNKNOWN",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_level(raw), expected, raw)

    def test_unknown_fields_are_not_invented(self):
        data = reconcile("99999999", [], now=NOW)
        self.assertEqual(data["brand"], "UNKNOWN")
        self.assertEqual(data["bank"], "UNKNOWN")
        self.assertEqual(data["phone"], "")
        self.assertEqual(data["sample_size"], 0)
        self.assertEqual(data["confidence"], 0.0)

    def test_sources_json_provenance(self):
        data = reconcile(
            "1",
            [
                ("binlist", 1.0, {"bank": "Chase"}),
                ("antipublic", 0.8, {"bank": "JPMorgan"}),
            ],
            now=NOW,
        )
        self.assertEqual(data["sources_json"]["bank"]["binlist"], "Chase")
        self.assertEqual(data["sources_json"]["bank"]["antipublic"], "JPMorgan")


if __name__ == "__main__":
    unittest.main()
