"""Three-state gate breaker (section 11).

CLOSED --3 faults--> OPEN(60s) --timer--> HALF_OPEN(1 probe)
HALF_OPEN --pass--> CLOSED ; HALF_OPEN --fail--> OPEN with capped backoff.

KEY_DEAD benches a key, never a gate. A DEAD verdict from a live canary card is
converted to GATE_FAULT by the canary job before it reaches this machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from .verdicts import Verdict, is_fault


class BreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Breaker:
    threshold: int = 3
    open_s: int = 60
    backoff_cap_s: int = 900
    state: BreakerState = BreakerState.CLOSED
    faults: int = 0
    backoff_s: int = 60
    opened_at: datetime | None = None

    def on_result(self, verdict: Verdict, now: datetime | None = None) -> BreakerState:
        at = now or _now()
        if verdict is Verdict.KEY_DEAD:
            return self.state
        if is_fault(verdict):
            self._fault(at)
            return self.state
        self.state = BreakerState.CLOSED
        self.faults = 0
        self.backoff_s = self.open_s
        self.opened_at = None
        return self.state

    def _fault(self, at: datetime) -> None:
        self.faults += 1
        if self.state is BreakerState.HALF_OPEN:
            self._open(at, double=True)
        elif self.state is BreakerState.CLOSED and self.faults >= self.threshold:
            self._open(at, double=False)

    def _open(self, at: datetime, *, double: bool) -> None:
        self.state = BreakerState.OPEN
        self.opened_at = at
        if double:
            self.backoff_s = min(self.backoff_s * 2, self.backoff_cap_s)

    def tick(self, now: datetime | None = None) -> BreakerState:
        at = now or _now()
        if (
            self.state is BreakerState.OPEN
            and self.opened_at is not None
            and at >= self.opened_at + timedelta(seconds=self.backoff_s)
        ):
            self.state = BreakerState.HALF_OPEN
        return self.state

    def force_probe(self) -> BreakerState:
        """Admin action: HALF_OPEN, never CLOSED directly."""
        self.state = BreakerState.HALF_OPEN
        return self.state

    @property
    def is_open(self) -> bool:
        return self.state is BreakerState.OPEN

    @property
    def seconds_until_probe(self) -> int:
        if self.state is not BreakerState.OPEN or self.opened_at is None:
            return 0
        remaining = (self.opened_at + timedelta(seconds=self.backoff_s) - _now()).total_seconds()
        return max(0, int(remaining))
