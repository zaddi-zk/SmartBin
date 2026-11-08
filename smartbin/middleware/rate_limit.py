from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message


class RateLimitMiddleware(BaseMiddleware):
    """Per-user token bucket on every entry point. Burst 5, refill 2/s."""

    def __init__(self, rate: float = 2.0, burst: int = 5):
        self.rate = rate
        self.burst = burst
        self._buckets: dict[int, tuple[float, float]] = {}

    def _allow(self, user_id: int) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(user_id, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - last) * self.rate)
        if tokens < 1.0:
            self._buckets[user_id] = (tokens, now)
            return False
        self._buckets[user_id] = (tokens - 1.0, now)
        return True

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        if self._allow(int(user.id)):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer("Slow down.", show_alert=False)
        elif isinstance(event, Message):
            await event.answer("Slow down.")
        return None
