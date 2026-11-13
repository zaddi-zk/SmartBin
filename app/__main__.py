"""Entry point: python -m app"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.config import load_settings
from app.db.seed import seed_gates
from app.db.session import init_db, session_scope
from app.jobs.breaker_tick import breaker_tick
from app.jobs.canary import canary_tick
from app.jobs.health_tick import health_tick
from app.logging import configure
from app.runtime import runtime
from app.tg.app import build_dispatcher

COMMANDS = [
    BotCommand(command="start", description="Main menu"),
    BotCommand(command="menu", description="Main menu"),
    BotCommand(command="bin", description="BIN lookup"),
    BotCommand(command="status", description="Gate status"),
    BotCommand(command="px", description="Proxies"),
    BotCommand(command="cancel", description="Cancel current flow"),
]


async def main() -> None:
    settings = load_settings()
    configure(settings.log_level)

    token = settings.tg_token.get_secret_value()
    if not token:
        raise SystemExit("TG_TOKEN/BOT_TOKEN is empty. Nothing to poll.")

    await init_db()
    async with session_scope() as session:
        await seed_gates(session)

    async with httpx.AsyncClient(timeout=15) as client, session_scope() as session:
        _promoted = await runtime().keys.validate_pending(session, client)

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(settings)
    await bot.set_my_commands(COMMANDS)

    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(health_tick(stop)),
        asyncio.create_task(breaker_tick(stop)),
        asyncio.create_task(canary_tick(stop)),
    ]
    try:
        await dp.start_polling(bot)
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
