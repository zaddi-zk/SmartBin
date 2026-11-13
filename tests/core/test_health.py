from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.health import RollingHealth, percentile

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def test_empty_windows_are_none() -> None:
    health = RollingHealth()
    assert health.p50() is None
    assert health.p95() is None
    assert health.success_pct() is None


def test_p50_uses_five_minute_window() -> None:
    health = RollingHealth()
    health.add(100, True, NOW - timedelta(minutes=10))
    health.add(200, True, NOW)
    health.add(300, True, NOW)
    assert health.p50(NOW) == 200


def test_p95_differs_from_p50() -> None:
    health = RollingHealth()
    for ms in (100, 110, 120, 130, 140, 900):
        health.add(ms, True, NOW)
    assert health.p50(NOW) == 120
    assert health.p95(NOW) == 900


def test_success_pct() -> None:
    health = RollingHealth()
    health.add(100, True, NOW)
    health.add(100, False, NOW)
    health.add(100, True, NOW)
    health.add(100, True, NOW)
    assert health.success_pct(NOW) == 75.0


def test_percentile_helper() -> None:
    assert percentile([], 0.5) is None
    assert percentile([1, 2, 3, 4], 0.5) == 2
    assert percentile([1, 2, 3, 4], 0.95) == 4
