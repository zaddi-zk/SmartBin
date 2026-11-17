"""Entry point: python -m app.

Single-instance bot rules (Render, long polling):

* The HTTP port is bound **first**. Render decides a deploy is live by reaching
  that port, and the boot work below (seeding, legacy import, key validation)
  can take seconds. Binding late makes a healthy deploy look like a dead one,
  which is how restart loops start.
* SIGTERM is handled by one event this module owns, and aiogram is told not to
  install its own handler. aiogram's flag lives inside the dispatcher, so a
  signal arriving during a retry sleep would be cleared by the next
  ``start_polling`` and the process would ignore the shutdown.
* Polling starts after a short delay. Render keeps the old instance alive until
  the new one is healthy, so the previous poller can still hold ``getUpdates``
  and Telegram answers 409 ("terminated by other getUpdates request").
* A failed ``start_polling`` retries with backoff instead of exiting, so a
  transient network error cannot turn into a Render restart loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal

import httpx
import structlog
from aiogram import Bot, Dispatcher
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
from app.db.migrate_legacy import migrate_legacy_users
from app.db.seed import (
    seed_adyen_keys,
    seed_authnet_keys,
    seed_braintree_keys,
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
from app.tg.description import description_tick
from app.webapp import build_app, mark_polling, public_base

log = structlog.get_logger("app")

# Telegram metadata calls must never hold the boot open.
BOOT_TIMEOUT_S = 15

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


def _retry_delay(attempt: int) -> float:
    """Backoff between polling start attempts: 5s, 10s, ... capped at 60s."""
    return min(60.0, 5.0 * max(1, attempt))


def _install_stop_handlers(stop: asyncio.Event) -> None:
    """Route SIGTERM/SIGINT into one event the whole process can see.

    aiogram is started with ``handle_signals=False`` so it cannot replace these
    handlers: a signal that arrives while this module is sleeping between retry
    attempts must still stop the process.
    """
    loop = asyncio.get_running_loop()

    def request_shutdown(sig: signal.Signals) -> None:
        log.info("event=shutdown_requested", signal=sig.name)
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, request_shutdown, sig)


async def _start_web(port: int) -> web.AppRunner:
    """Bind the HTTP port immediately; return the runner that owns it.

    A bind failure raises here, at the top of boot, instead of disappearing
    inside a background task. Render then sees a clear crash rather than an
    unexplained restart loop.
    """
    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)  # noqa: S104
    await site.start()
    log.info("event=http_listening", port=port)
    return runner


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
                ),
                request_timeout=BOOT_TIMEOUT_S,
            )
        else:
            # No public https origin known: fall back to the command list
            # rather than leave a dead WebApp button in place.
            await bot.set_chat_menu_button(
                menu_button=MenuButtonCommands(), request_timeout=BOOT_TIMEOUT_S
            )
    except Exception as exc:
        # A menu button is cosmetic; never let it block boot.
        log.warning("event=set_menu_failed", error=str(exc))


async def _poll_once(dp: Dispatcher, bot: Bot, stop: asyncio.Event) -> None:
    """Poll until ``stop`` fires or the poller ends on its own."""
    polling = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False, close_bot_session=False)
    )
    shutdown = asyncio.create_task(stop.wait())
    mark_polling(True)
    try:
        done, _ = await asyncio.wait(
            {polling, shutdown}, return_when=asyncio.FIRST_COMPLETED
        )
        stopping = shutdown in done and not polling.done()

        if stopping:
            # Ask aiogram to unwind: it cancels the in-flight getUpdates, which
            # is what releases the token for the next instance.
            with contextlib.suppress(RuntimeError):
                await dp.stop_polling()
            if not polling.done():
                polling.cancel()

        shutdown.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await shutdown

        if stopping:
            # The poller was stopped on purpose; a CancelledError here is the
            # expected shutdown path, not a failure to report.
            with contextlib.suppress(asyncio.CancelledError):
                await polling
            return
        await polling
    finally:
        mark_polling(False)


async def _poll_forever(
    dp: Dispatcher, bot: Bot, stop: asyncio.Event, retries: int
) -> None:
    """Supervise polling: retry a failed start instead of exiting the process.

    aiogram already retries ``getUpdates`` errors internally (including the 409
    conflict), so an exception here means the poller could not even start — an
    unreachable Telegram or a bad token. A few retries keep a transient failure
    from becoming a Render restart loop; a bad token still ends the process with
    a clear log instead of a silent zombie.
    """
    attempt = 0
    while not stop.is_set():
        try:
            await _poll_once(dp, bot, stop)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            attempt += 1
            log.error(
                "event=polling_failed",
                attempt=attempt,
                error=f"{type(exc).__name__}: {exc}",
            )
            if stop.is_set():
                return
            # ``poll_retries`` counts attempts, so 0 means one attempt, no retry.
            if attempt >= max(1, retries):
                raise
            delay = _retry_delay(attempt)
            log.warning("event=polling_retry", attempt=attempt, in_s=delay)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)


async def main() -> None:
    settings = load_settings()
    configure(settings.log_level)

    runner = await _start_web(_health_port())
    stop = asyncio.Event()
    _install_stop_handlers(stop)
    tasks: list[asyncio.Task[None]] = []
    bot: Bot | None = None
    try:
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
            await seed_braintree_keys(session, settings.braintree_key_list())
            await repo.enable_installed_gates(session, set(runtime().adapters.names))
            seeded_proxies = await runtime().proxies.seed_from_env(session)
        if seeded_proxies:
            log.info("event=proxy_seed", added=seeded_proxies)

        # Import legacy Telegram users once at boot. The pass is idempotent and
        # read-only against Supabase, so a failure must never stop the bot.
        legacy_url = settings.legacy_source_url
        if legacy_url:
            try:
                async with session_scope() as session:
                    report = await migrate_legacy_users(session, legacy_url)
                log.info(
                    "event=legacy_import",
                    read=report.read,
                    created=report.created,
                    updated=report.updated,
                    error=report.error,
                )
            except Exception as exc:
                log.warning("event=legacy_import_failed", error=str(exc))

        async with httpx.AsyncClient(timeout=BOOT_TIMEOUT_S) as client, session_scope() as session:
            _promoted = await runtime().keys.validate_pending(session, client)

        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = build_dispatcher(settings)

        # A webhook makes getUpdates answer 409 forever, and the legacy bot may
        # have left one behind. Clear it once; queued user messages are kept.
        await bot.delete_webhook(
            drop_pending_updates=False, request_timeout=BOOT_TIMEOUT_S
        )
        await bot.set_my_commands(COMMANDS, request_timeout=BOOT_TIMEOUT_S)
        await _set_menu_button(bot)
        me = await bot.get_me(request_timeout=BOOT_TIMEOUT_S)
        log.info("event=poller_identity", bot_id=me.id, username=me.username)

        tasks = [
            asyncio.create_task(health_tick(stop)),
            asyncio.create_task(breaker_tick(stop)),
            asyncio.create_task(canary_tick(stop)),
            asyncio.create_task(proxy_tick(stop)),
            asyncio.create_task(description_tick(bot, stop)),
        ]

        # Let the previous instance finish its SIGTERM shutdown before taking
        # the polling session; otherwise the first getUpdates is a 409.
        if settings.poll_start_delay_s > 0 and not stop.is_set():
            log.info("event=poll_start_delay", seconds=settings.poll_start_delay_s)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.poll_start_delay_s)

        await _poll_forever(dp, bot, stop, settings.poll_retries)
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if bot is not None:
            await bot.session.close()
        await runner.cleanup()
        log.info("event=shutdown")


if __name__ == "__main__":
    asyncio.run(main())
