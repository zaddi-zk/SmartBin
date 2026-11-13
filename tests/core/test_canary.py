from __future__ import annotations

from app.core.canary import DEGRADED, HEALTHY, OPEN, as_fault, evaluate, prune_candidates
from app.core.verdicts import Verdict


def test_three_healthy() -> None:
    assert evaluate([Verdict.APPROVED, Verdict.APPROVED, Verdict.APPROVED]) == HEALTHY


def test_one_or_two_healthy_is_degraded_not_open() -> None:
    assert evaluate([Verdict.APPROVED, Verdict.DEAD, Verdict.DEAD]) == DEGRADED
    assert evaluate([Verdict.LIVE, Verdict.DEAD, Verdict.DEAD]) == DEGRADED


def test_zero_healthy_opens() -> None:
    assert evaluate([Verdict.DEAD, Verdict.RETRY, Verdict.DEAD]) == OPEN


def test_dead_canary_becomes_gate_fault() -> None:
    assert as_fault(Verdict.DEAD) is Verdict.GATE_FAULT
    assert as_fault(Verdict.APPROVED) is Verdict.APPROVED


def test_bad_card_pruned_on_three_gates() -> None:
    failures = {"card-a": 3, "card-b": 2, "card-c": 5}
    assert prune_candidates(failures) == ["card-a", "card-c"]
