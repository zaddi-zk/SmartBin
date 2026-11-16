"""Entry point: python -m app"""

from __future__ import annotations

import asyncio
import contextlib
import os

import httpx
import structlog
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    MenuButtonCommands,
    MenuButtonWebApp,
    WebAppInfo,
)
from aiohttp import web

from app.config import load_settings
from app.db import repo
from app.db.seed import (
    seed_adyen_keys,
    seed_authnet_keys,
    seed_checkout_keys,
    seed_cybersource_keys,
    seed_gates,
    seed_stripe_keys,
)
from app.db.session import init_db, session_scope
from app.jobs.breaker_tick import breaker_tick
from app.jobs.canary import canary_tick
from app.jobs.health_tick import health_tick
from app.jobs.proxy_tick import proxy_tick
from app.logging import configure
from app.runtime import runtime
from app.tg.app import build_dispatcher
from app.webapp import build_app, public_base

log = structlog.get_logger("app")

COMMANDS = [
    BotCommand(command="start", description="Main menu"),
    BotCommand(command="menu", description="Main menu"),
    BotCommand(command="bin", description="BIN lookup"),
    BotCommand(command="status", description="Gate status"),
    BotCommand(command="px", description="Proxies"),
    BotCommand(command="cancel", description="Cancel current flow"),
]


def _health_port() -> int:
    """Render port-scan port: PORT, then the legacy MINIAPP_PORT, then 8080."""
    for name in ("PORT", "MINIAPP_PORT"):
        raw = os.environ.get(name, "").strip()
        if raw.isdigit():
            return int(raw)
    return 8080


async def _set_menu_button(bot: Bot) -> None:
    """Point the menu button at this service, never at a stale legacy URL.

    The legacy bot left a ``MenuButtonWebApp`` on Telegram's side. If it is not
    replaced, every user who taps the menu lands on whatever the old URL now
    answers — which is how the raw health JSON ended up in front of users.
    """
    base = public_base()
    try:
        if base:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Smart Bin", web_app=WebAppInfo(url=base)
                )
            )
        else:
            # No public https origin known: fall back to the command list
            # rather than leave a dead WebApp button in place.
            await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as exc:
        # A menu button is cosmetic; never let it block boot.
        log.warning("event=set_menu_failed", error=str(exc))


async def _serve_web(port: int) -> None:
    runner = web.AppRunner(build_app())
    await runner.setup()
    # 0.0.0.0 is required: Render's port scan hits the container's public IP.
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
    await site.start()
    await asyncio.Event().wait()  # serve until cancelled


async def main() -> None:
    settings = load_settings()
    configure(settings.log_level)

    token = settings.tg_token.get_secret_value()
    if not token:
        raise SystemExit("TG_TOKEN/BOT_TOKEN is empty. Nothing to poll.")

    await init_db()
    async with session_scope() as session:
        await seed_gates(session)
        await seed_stripe_keys(session, settings.stripe_key_list())
        await seed_adyen_keys(session, settings.adyen_key_list())
        await seed_authnet_keys(session, settings.authnet_key_list())
        await seed_checkout_keys(session, settings.checkout_key_list())
        await seed_cybersource_keys(session, settings.cybersource_key_list())
        await repo.enable_installed_gates(session, set(runtime().adapters.names))
        seeded_proxies = await runtime().proxies.seed_from_env(session)
    if seeded_proxies:
        log.info("event=proxy_seed", added=seeded_proxies)

    async with httpx.AsyncClient(timeout=15) as client, session_scope() as session:
        _promoted = await runtime().keys.validate_pending(session, client)

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(settings)
    await bot.set_my_commands(COMMANDS)
    await _set_menu_button(bot)

    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(health_tick(stop)),
        asyncio.create_task(breaker_tick(stop)),
        asyncio.create_task(canary_tick(stop)),
        asyncio.create_task(proxy_tick(stop)),
        asyncio.create_task(_serve_web(_health_port())),
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
