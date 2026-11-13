"""Canary evaluation (section 12) — pure, so the prune rule is testable.

3/3 healthy -> HEALTHY. 1-2/3 -> DEGRADED (still routable). 0/3 with fault
classes -> OPEN. A card that fails on three or more gates is a bad card, not
three bad gates: prune the card and leave the gates alone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.core.verdicts import Verdict

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
OPEN = "OPEN"


def is_healthy(result: Verdict) -> bool:
    return result in (Verdict.APPROVED, Verdict.LIVE, Verdict.LIVE_3DS)


def evaluate(results: Iterable[Verdict]) -> str:
    verdicts = list(results)
    if not verdicts:
        return DEGRADED
    healthy = sum(1 for verdict in verdicts if is_healthy(verdict))
    if healthy == len(verdicts):
        return HEALTHY
    if healthy >= 1:
        return DEGRADED
    return OPEN


def prune_candidates(failures_by_card: Mapping[str, int], threshold: int = 3) -> list[str]:
    """Cards failing on ``threshold`` or more gates are bad canaries."""
    return sorted(card for card, gates in failures_by_card.items() if gates >= threshold)


def as_fault(result: Verdict) -> Verdict:
    """A DEAD on a known-live canary is a gate fault, never a dead canary."""
    if result is Verdict.DEAD:
        return Verdict.GATE_FAULT
    return result
