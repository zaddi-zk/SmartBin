"""Cross-cutting middlewares: ack, debounce, rate, session.

Section 3.11: answerCallbackQuery fires before any handler runs. That is the
only reliable way to stay under 300 ms, including on guard refusal.
"""

from __future__ import annotations

import contextlib
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import Settings
from app.core.parse import Card, CardError
from app.db.session import session_scope


class AckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            with contextlib.suppress(TelegramBadRequest):
                await event.answer()
        return await handler(event, data)


class DebounceMiddleware(BaseMiddleware):
    def __init__(self, window_ms: int = 800) -> None:
        self.window = window_ms / 1000
        self.last: dict[tuple[int, str], float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery) and event.from_user and event.data:
            key = (event.from_user.id, event.data)
            now = time.monotonic()
            previous = self.last.get(key, 0.0)
            if now - previous < self.window:
                return None
            self.last[key] = now
        return await handler(event, data)


class RateMiddleware(BaseMiddleware):
    """30 card submissions per minute (section 22), not 30 taps.

    Counting every callback would throttle ordinary navigation; the debounce
    middleware already protects buttons from double taps.
    """

    def __init__(self, settings: Settings) -> None:
        self.limit = settings.rate_per_min
        self.hits: dict[int, deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None or not event.text:
            return await handler(event, data)
        try:
            Card.parse(event.text)
        except CardError:
            return await handler(event, data)

        now = time.monotonic()
        hits = self.hits[event.from_user.id]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self.limit:
            text = (
                "🐢 <b>SLOW DOWN</b>\n"
                "<blockquote>30 checks/min. Try again in a moment.</blockquote>"
            )
            await event.answer(text)
            return None
        hits.append(now)
        return await handler(event, data)


class SessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_scope() as session:
            data["session"] = session
            return await handler(event, data)
