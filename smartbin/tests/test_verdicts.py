from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.gate import TEST_CARDS  # noqa: E402
from core.twofa import TwoFARead, evaluate  # noqa: E402
from core.verdicts import map_result  # noqa: E402


def rec(**kw):
    base = {
        "twofa": "UNKNOWN",
        "twofa_conf": 0.0,
        "twofa_reads": 0,
        "last_twofa_utc": "",
        "amt_tested": 0,
    }
    base.update(kw)
    return base


def hist(read, gate="stripe", ts="2026-01-15T00:00:00Z", amount=50, canary=1):
    return {
        "twofa_read": read,
        "gate": gate,
        "created_utc": ts,
        "amount_band": amount,
        "canary_ok": canary,
        "acs_host": "",
    }


class VerdictTests(unittest.TestCase):
    def test_stripe_test_card_matrix_exact(self):
        expected = {
            "4242424242424242": "APPROVED",
            "4000000000000002": "DECLINED",  # was AMBIGUOUS - peak mode
            "4000000000009995": "INSUFFICIENT",
            "4000000000009987": "PICKUP",
            "4000000000000069": "EXPIRED",
            "4000000000000127": "CVV-FAIL",
            "4000000000003220": "3DS-CHALLENGE",
        }
        for number, result in TEST_CARDS.items():
            outcome = map_result(result, canary_ok=1)
            self.assertEqual(outcome.verdict, expected[number], number)

    def test_network_matrix(self):
        cases = {
            "00": "APPROVED",
            "05": "DO-NOT-HONOR",
            "51": "INSUFFICIENT",
            "14": "INVALID",
            "56": "INVALID",
            "54": "EXPIRED",
            "41": "PICKUP",
            "43": "PICKUP",
            "82": "CVV-FAIL",
            "N7": "CVV-FAIL",
            "57": "MERCHANT-RESTRICTED",
            "62": "MERCHANT-RESTRICTED",
            "Z3": "MERCHANT-RESTRICTED",
        }
        for code, verdict in cases.items():
            self.assertEqual(map_result({"raw_code": code}, 1).verdict, verdict, code)

    def test_stripe_generic_decline_never_masquerades(self):
        outcome = map_result({"decline_code": "generic_decline"}, 1)
        self.assertEqual(outcome.verdict, "DECLINED")  # was AMBIGUOUS - peak mode
        self.assertNotEqual(outcome.verdict, "DO-NOT-HONOR")
        self.assertEqual(map_result({"decline_code": "fraudulent"}, 1).verdict, "DECLINED")  # was AMBIGUOUS

    def test_canary_fail_forces_inconclusive(self):
        outcome = map_result({"decline_code": "generic_decline"}, 0)
        self.assertEqual(outcome.verdict, "INCONCLUSIVE")

    def test_05_in_failed_canary_window_is_ambiguous(self):
        outcome = map_result({"raw_code": "05"}, 0)
        self.assertEqual(outcome.verdict, "AMBIGUOUS")

    def test_timeout_retries_once(self):
        outcome = map_result({"timeout": True}, 1)
        self.assertEqual(outcome.verdict, "INCONCLUSIVE")
        self.assertTrue(outcome.retry)

    def test_unknown_code_is_ambiguous(self):
        self.assertEqual(map_result({"raw_code": "ZZ"}, 1).verdict, "AMBIGUOUS")


class TwoFARuleTests(unittest.TestCase):
    def test_r1_real_acs_host_is_vbv(self):
        read = TwoFARead(kind="challenge", gate="stripe", ts="2026-01-15T00:00:00Z",
                         amount_minor=50, canary_ok=True, acs_host="acs.bank.example", iso2="US")
        result = evaluate(rec(), read, [])
        self.assertTrue(result["write"])
        self.assertEqual(result["twofa"], "VBV")

    def test_r2_timeout_never_writes(self):
        read = TwoFARead(kind="timeout", ts="2026-01-15T00:00:00Z", amount_minor=50)
        result = evaluate(rec(), read, [])
        self.assertFalse(result["write"])

    def test_r3_soft_decline_without_acs_never_writes(self):
        read = TwoFARead(kind="soft_decline", ts="2026-01-15T00:00:00Z", amount_minor=50)
        result = evaluate(rec(), read, [])
        self.assertFalse(result["write"])

    def test_r4_eea_below_3100_is_exemption(self):
        read = TwoFARead(kind="nonvbv", gate="stripe", ts="2026-01-15T00:00:00Z",
                         amount_minor=100, canary_ok=True, iso2="DK")
        result = evaluate(rec(), read, [])
        self.assertFalse(result["write"])
        self.assertEqual(result["check_read"], "EXEMPTION")

    def test_r5_nonvbv_requires_clean_canary(self):
        read = TwoFARead(kind="nonvbv", gate="stripe", ts="2026-01-15T00:00:00Z",
                         amount_minor=50, canary_ok=False, iso2="US")
        result = evaluate(rec(), read, [])
        self.assertFalse(result["write"])
        self.assertEqual(result["check_read"], "UNVERIFIED")

    def test_r6_same_gate_six_hours_promotes_to_asterisk(self):
        read = TwoFARead(kind="nonvbv", gate="stripe", ts="2026-01-15T06:00:00Z",
                         amount_minor=50, canary_ok=True, iso2="US")
        result = evaluate(rec(twofa="LOW-CONF", twofa_reads=1), read, [hist("LOW-CONF")])
        self.assertTrue(result["write"])
        self.assertEqual(result["twofa"], "NON-VBV*")

    def test_r6_two_gates_promotes_without_asterisk(self):
        read = TwoFARead(kind="nonvbv", gate="stripe", ts="2026-01-15T01:00:00Z",
                         amount_minor=50, canary_ok=True, iso2="US")
        result = evaluate(
            rec(twofa="LOW-CONF", twofa_reads=1),
            read,
            [hist("LOW-CONF", gate="other-gate")],
        )
        self.assertTrue(result["write"])
        self.assertEqual(result["twofa"], "NON-VBV")

    def test_r7_single_read_stays_low_conf(self):
        read = TwoFARead(kind="nonvbv", gate="stripe", ts="2026-01-15T00:00:00Z",
                         amount_minor=50, canary_ok=True, iso2="US")
        result = evaluate(rec(), read, [])
        self.assertTrue(result["write"])
        self.assertEqual(result["twofa"], "LOW-CONF")

    def test_r8_challenge_downgrades_nonvbv(self):
        read = TwoFARead(kind="challenge", gate="stripe", ts="2026-01-15T00:00:00Z",
                         amount_minor=50, canary_ok=True, acs_host="acs.example", iso2="US")
        result = evaluate(rec(twofa="NON-VBV", twofa_conf=80.0, twofa_reads=2), read, [])
        self.assertTrue(result["write"])
        self.assertEqual(result["twofa"], "VBV")
        self.assertIn("R8", result["note"])

    def test_r9_partial_requires_disagreement_at_one_band(self):
        read = TwoFARead(kind="nonvbv", gate="stripe", ts="2026-01-15T01:00:00Z",
                         amount_minor=50, canary_ok=True, iso2="US")
        result = evaluate(
            rec(twofa="VBV", twofa_conf=95.0, twofa_reads=1),
            read,
            [hist("VBV", amount=50)],
        )
        self.assertTrue(result["write"])
        self.assertEqual(result["twofa"], "PARTIAL")

    def test_r10_captcha_penalizes_gate_not_bin(self):
        read = TwoFARead(kind="captcha", gate="stripe", gate_kind="stripe",
                         ts="2026-01-15T00:00:00Z", amount_minor=50)
        result = evaluate(rec(), read, [])
        self.assertFalse(result["write"])
        self.assertEqual(result["check_read"], "POSSIBLE-CAPTCHA")
        self.assertTrue(result["gate_penalty"])

    def test_eea_enrollment_at_3100_writes(self):
        read = TwoFARead(kind="challenge", gate="stripe", ts="2026-01-15T00:00:00Z",
                         amount_minor=3100, canary_ok=True, acs_host="acs.example", iso2="DK")
        result = evaluate(rec(), read, [])
        self.assertTrue(result["write"])
        self.assertEqual(result["amt_tested"], 3100)


if __name__ == "__main__":
    unittest.main()
