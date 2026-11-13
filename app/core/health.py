"""Rolling health from real samples (section 3.8 / 23).

Never a hardcoded percentile. p50 uses the last 5 minutes, p95 the last 30,
success the last 24 hours. If a window is empty the metric is ``None`` — the UI
shows ``—`` rather than inventing a number.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

P50_WINDOW_S = 300
P95_WINDOW_S = 1_800
SUCCESS_WINDOW_S = 86_400


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Sample:
    at: datetime
    ms: int
    ok: bool


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


@dataclass(slots=True)
class RollingHealth:
    samples: deque[Sample] = field(default_factory=deque)
    max_samples: int = 5_000

    def add(self, ms: int, ok: bool, at: datetime | None = None) -> None:
        self.samples.append(Sample(at=at or _now(), ms=max(0, int(ms)), ok=ok))
        while len(self.samples) > self.max_samples:
            self.samples.popleft()

    def extend(self, samples: Iterable[Sample]) -> None:
        for sample in samples:
            self.add(sample.ms, sample.ok, sample.at)

    def _window(self, seconds: int, at: datetime | None = None) -> list[Sample]:
        cutoff = (at or _now()) - timedelta(seconds=seconds)
        return [s for s in self.samples if s.at >= cutoff]

    def p50(self, at: datetime | None = None) -> int | None:
        return percentile([s.ms for s in self._window(P50_WINDOW_S, at)], 0.50)

    def p95(self, at: datetime | None = None) -> int | None:
        return percentile([s.ms for s in self._window(P95_WINDOW_S, at)], 0.95)

    def success_pct(self, at: datetime | None = None) -> float | None:
        window = self._window(SUCCESS_WINDOW_S, at)
        if not window:
            return None
        return round(100.0 * sum(1 for s in window if s.ok) / len(window), 2)

    def count(self, seconds: int, at: datetime | None = None) -> int:
        return len(self._window(seconds, at))

    @property
    def total(self) -> int:
        return len(self.samples)
