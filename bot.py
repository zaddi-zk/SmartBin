from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent, MenuButtonWebApp, WebAppInfo

import sys
sys.path.insert(0, 'smartbin')

from config import Config
from handlers import setup_routers
from middleware.rate_limit import RateLimitMiddleware
from miniapp.server import start_server
from runtime import build_runtime, close_runtime

log = logging.getLogger("smartbin.bot")

COMMANDS = [
    BotCommand(command="start", description="Open main menu"),
    BotCommand(command="menu", description="Return to menu"),
]


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        format="ts=%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s",
    )
    logging.getLogger("aiogram").setLevel(logging.WARNING)


async def configure_bot(bot: Bot, config: Config) -> None:
    try:
        await asyncio.wait_for(bot.set_my_commands(COMMANDS), timeout=5.0)
    except Exception as exc:
        log.warning("event=set_commands_failed error=%s", exc)
    if not config.miniapp_base.lower().startswith("https://"):
        log.warning("event=menu_skipped reason=non_https base=%s", config.miniapp_base)
        return
    try:
        await asyncio.wait_for(
            bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="SMARTBIN",
                    web_app=WebAppInfo(url=config.miniapp_base),
                )
            ),
            timeout=5.0,
        )
    except Exception as exc:
        log.warning("event=set_menu_failed error=%s", exc)


async def on_error(event: ErrorEvent) -> None:
    """Structured error log. No tracebacks on transient network faults."""
    update_id = getattr(getattr(event, "update", None), "update_id", 0)
    log.error("event=handler_failed update_id=%s error=%s", update_id, event.exception)


async def main() -> int:
    config = Config.load()
    setup_logging(config.log_level)
    if not config.bot_token:
        log.error("event=boot_failed reason=BOT_TOKEN_missing")
        return 1

    runtime = await build_runtime(config)
    bot = Bot(token=config.bot_token)
    runtime.bot = bot
    dp = Dispatcher(storage=MemoryStorage())
    dp["runtime"] = runtime

    limiter = RateLimitMiddleware()
    dp.message.outer_middleware(limiter)
    dp.callback_query.outer_middleware(limiter)
    dp.inline_query.outer_middleware(limiter)

    setup_routers(dp)
    dp.errors.register(on_error)
    runtime.miniapp_runner = await start_server(runtime)
    runtime.canary.start()

    log.info(
        "event=boot version=%s miniapp_port=%s db=%s lru=%s",
        config.display_version,
        config.miniapp_port,
        config.db_path,
        runtime.store.depth(),
    )
    await configure_bot(bot, config)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("event=polling_failed error=%s", exc)
        return 1
    finally:
        await close_runtime(runtime)
        await bot.session.close()
        log.info("event=shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
