from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

import httpx

from util_net import fetch_json


class CircuitBreaker:
    """5 consecutive fails opens the breaker for 10 minutes."""

    def __init__(self, threshold: int = 5, reset_seconds: float = 600.0):
        self.threshold = threshold
        self.reset_seconds = reset_seconds
        self.fails = 0
        self.open_until = 0.0

    def allow(self) -> bool:
        return time.monotonic() >= self.open_until

    def record_success(self) -> None:
        self.fails = 0
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.fails += 1
        if self.fails >= self.threshold:
            self.open_until = time.monotonic() + self.reset_seconds

    @property
    def is_open(self) -> bool:
        return not self.allow()


@runtime_checkable
class Source(Protocol):
    name: str
    weight: float

    async def fetch(self, bin8: str, proxy: str | None = None) -> dict[str, Any] | None: ...


class BaseSource:
    name = "base"
    weight = 1.0
    timeout = 6.0

    def __init__(self) -> None:
        self.breaker = CircuitBreaker()

    async def fetch(self, bin8: str, proxy: str | None = None) -> dict[str, Any] | None:
        raise NotImplementedError

    async def safe_fetch(self, bin8: str, proxy: str | None = None) -> dict[str, Any] | None:
        if not self.breaker.allow():
            return None
        try:
            result = await self.fetch(bin8, proxy=proxy)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            self.breaker.record_failure()
            return None
        except Exception:
            self.breaker.record_failure()
            return None
        if result:
            self.breaker.record_success()
        else:
            self.breaker.record_failure()
        return result

    async def _get_json(
        self, url: str, proxy: str | None = None, headers: dict[str, str] | None = None
    ) -> Any:
        return await fetch_json(url, proxy=proxy, timeout=self.timeout, headers=headers)
