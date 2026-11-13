"""Dispatcher assembly."""

from __future__ import annotations

from aiogram import Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import Settings
from app.tg.middleware import AckMiddleware, DebounceMiddleware, RateMiddleware, SessionMiddleware
from app.tg.screens import admin, armed, gates, mass, menu, proxy, status
from app.tg.screens import bin as bin_screen


def build_dispatcher(settings: Settings) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["settings"] = settings

    # Ack first: the spinner must clear before any database or network work.
    dp.callback_query.outer_middleware(AckMiddleware())
    dp.callback_query.outer_middleware(DebounceMiddleware(settings.debounce_ms))
    dp.callback_query.outer_middleware(RateMiddleware(settings))
    dp.callback_query.outer_middleware(SessionMiddleware())

    dp.message.outer_middleware(RateMiddleware(settings))
    dp.message.outer_middleware(SessionMiddleware())

    dp.include_routers(
        menu.router,
        gates.router,
        armed.router,
        status.router,
        bin_screen.router,
        proxy.router,
        mass.router,
        admin.router,
        _fallback_router(),
    )
    return dp


def _fallback_router() -> Router:
    from app.tg.screens.fallback import router

    return router
