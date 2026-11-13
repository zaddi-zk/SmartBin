"""Verdict enum and the charge matrix (section 7).

A verdict is the only thing that may move credits. ``TERMINAL`` is the set that
counts as an answer; everything else is free by construction.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum


class Verdict(StrEnum):
    APPROVED = "APPROVED"
    LIVE = "LIVE"
    LIVE_3DS = "LIVE_3DS"
    DEAD = "DEAD"
    RETRY = "RETRY"
    KEY_DEAD = "KEY_DEAD"
    GATE_FAULT = "GATE_FAULT"
    INCONCLUSIVE = "INCONCLUSIVE"


TERMINAL: frozenset[Verdict] = frozenset(
    {Verdict.APPROVED, Verdict.LIVE, Verdict.LIVE_3DS, Verdict.DEAD}
)

# Decimal, never float: credits are money.
CHARGE: dict[Verdict, Decimal] = {
    Verdict.APPROVED: Decimal("1.0"),
    Verdict.LIVE: Decimal("1.0"),
    Verdict.LIVE_3DS: Decimal("0.5"),
    Verdict.DEAD: Decimal("0"),
    Verdict.RETRY: Decimal("0"),
    Verdict.KEY_DEAD: Decimal("0"),
    Verdict.GATE_FAULT: Decimal("0"),
    Verdict.INCONCLUSIVE: Decimal("0"),
}


def charge_for(verdict: Verdict) -> Decimal:
    return CHARGE[verdict]


def is_terminal(verdict: Verdict) -> bool:
    return verdict in TERMINAL


def is_fault(verdict: Verdict) -> bool:
    """Section 11: a gate fault is RETRY/INCONCLUSIVE from the gate itself.

    ``GATE_FAULT`` is the executor's roll-up of those two, so it must count as
    a fault too; otherwise a gate that fails before dispatch never opens.
    """
    return verdict in (Verdict.RETRY, Verdict.INCONCLUSIVE, Verdict.GATE_FAULT)
