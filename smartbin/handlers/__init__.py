from __future__ import annotations

from aiogram import Dispatcher

from . import admin, bin_lookup, bulk, check, flows, pay, start, status


def setup_routers(dp: Dispatcher) -> None:
    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(bin_lookup.router)
    dp.include_router(check.router)
    dp.include_router(bulk.router)
    dp.include_router(status.router)
    dp.include_router(pay.router)
    dp.include_router(flows.router)
