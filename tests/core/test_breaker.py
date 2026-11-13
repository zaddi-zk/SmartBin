from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.breaker import Breaker, BreakerState
from app.core.verdicts import Verdict

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def test_three_faults_open_the_gate() -> None:
    breaker = Breaker(threshold=3, open_s=60)
    assert breaker.on_result(Verdict.RETRY, NOW) is BreakerState.CLOSED
    assert breaker.on_result(Verdict.RETRY, NOW) is BreakerState.CLOSED
    assert breaker.on_result(Verdict.RETRY, NOW) is BreakerState.OPEN
    assert breaker.faults == 3


def test_terminal_result_resets() -> None:
    breaker = Breaker(threshold=3)
    breaker.on_result(Verdict.RETRY, NOW)
    breaker.on_result(Verdict.RETRY, NOW)
    assert breaker.on_result(Verdict.DEAD, NOW) is BreakerState.CLOSED
    assert breaker.faults == 0


def test_open_to_half_open_after_backoff() -> None:
    breaker = Breaker(threshold=3, open_s=60)
    for _ in range(3):
        breaker.on_result(Verdict.RETRY, NOW)
    assert breaker.state is BreakerState.OPEN
    assert breaker.tick(NOW + timedelta(seconds=59)) is BreakerState.OPEN
    assert breaker.tick(NOW + timedelta(seconds=60)) is BreakerState.HALF_OPEN


def test_half_open_pass_closes() -> None:
    breaker = Breaker(threshold=1, open_s=60)
    breaker.on_result(Verdict.RETRY, NOW)
    breaker.tick(NOW + timedelta(seconds=61))
    assert breaker.on_result(Verdict.APPROVED, NOW + timedelta(seconds=61)) is BreakerState.CLOSED


def test_half_open_fail_doubles_backoff_and_caps() -> None:
    breaker = Breaker(threshold=1, open_s=60, backoff_cap_s=900)
    breaker.on_result(Verdict.RETRY, NOW)
    breaker.tick(NOW + timedelta(seconds=61))
    breaker.on_result(Verdict.RETRY, NOW + timedelta(seconds=61))
    assert breaker.state is BreakerState.OPEN
    assert breaker.backoff_s == 120
    for step in range(10):
        at = NOW + timedelta(hours=1 + step)
        breaker.tick(at)
        breaker.on_result(Verdict.RETRY, at)
    assert breaker.backoff_s == 900


def test_gate_fault_counts_towards_breaker() -> None:
    breaker = Breaker(threshold=2)
    assert breaker.on_result(Verdict.GATE_FAULT, NOW) is BreakerState.CLOSED
    assert breaker.on_result(Verdict.GATE_FAULT, NOW) is BreakerState.OPEN


def test_key_dead_does_not_touch_gate() -> None:
    breaker = Breaker(threshold=1)
    assert breaker.on_result(Verdict.KEY_DEAD, NOW) is BreakerState.CLOSED
    assert breaker.faults == 0


def test_force_probe_is_half_open_not_closed() -> None:
    breaker = Breaker()
    assert breaker.force_probe() is BreakerState.HALF_OPEN
