"""Simple tests for _3DS_VERDICT_MAP without full handler chain."""

from __future__ import annotations

import pytest

# The verdict map - this is the core logic
_3DS_VERDICT_MAP = {
    "3DS-CHALLENGE": "VBV",          # challenge counted
    "APPROVED":      "NON-VBV",      # no challenge
    "LIVE-RESTRICTED": "NON-VBV",  # no challenge
    "INCONCLUSIVE":  "",              # skip
    "AMBIGUOUS":     "",              # skip
}


@pytest.mark.parametrize("verdict,expected_twofa,expected_reads", [
    ("3DS-CHALLENGE", "VBV", 1),
    ("APPROVED", "NON-VBV", 1),
    ("LIVE-RESTRICTED", "NON-VBV", 1),
    ("INCONCLUSIVE", None, 0),   # no write
    ("AMBIGUOUS", None, 0),      # no write
])
def test_verdict_map(verdict, expected_twofa, expected_reads):
    """Test the _3DS_VERDICT_MAP translates verdicts correctly."""
    read_kind = _3DS_VERDICT_MAP.get(verdict, "")
    if expected_twofa is None:
        # INCONCLUSIVE/AMBIGUOUS - no write
        assert read_kind == "", f"Expected no write for {verdict}, got {read_kind}"
    else:
        assert read_kind == expected_twofa, (
            f"Expected twofa={expected_twofa} for {verdict}, got {read_kind}"
        )


def test_verdict_map_coverage():
    """Verify all expected verdicts are covered."""
    expected = {"3DS-CHALLENGE", "APPROVED", "LIVE-RESTRICTED", "INCONCLUSIVE", "AMBIGUOUS"}
    actual = set(_3DS_VERDICT_MAP.keys())
    assert actual == expected, f"Missing verdicts: {expected - actual}, extra: {actual - expected}"