from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware


class ActivityMiddleware(BaseMiddleware):
    """Bump users.last_seen_utc on every interaction (throttled in the DB).

    This keeps the public "monthly users" number accurate even for users who
    only send plain text or tap inline buttons.
    """

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        runtime = data.get("runtime")
        user = getattr(event, "from_user", None)
        if runtime is not None and user is not None and not getattr(user, "is_bot", False):
            try:
                await runtime.db.touch_user(
                    int(user.id), user.username or "", user.first_name or ""
                )
            except Exception:
                # Activity tracking must never block a handler.
                pass
        return await handler(event, data)
