"""Long polling on a single instance: retry, bind early, and stop cleanly.

These cover the Option-2 rules in one place: the process must not die on a
failed poll start, the HTTP port must be live before the slow boot work, and a
SIGTERM must actually end the poller instead of being swallowed by a retry.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import httpx
import pytest
from app.__main__ import _poll_forever, _retry_delay, _start_web
from app.config import Settings


class FlakyDispatcher:
    """Starts polling, failing the first ``failures`` attempts."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.starts = 0
        self.stops = 0

    async def start_polling(
        self,
        bot: Any,
        *,
        handle_signals: bool = True,
        close_bot_session: bool = True,
    ) -> None:
        assert handle_signals is False, "the app owns SIGTERM, not aiogram"
        assert close_bot_session is False, "the app closes the session once"
        self.starts += 1
        if self.starts <= self.failures:
            raise RuntimeError("telegram unreachable")


class BlockingDispatcher(FlakyDispatcher):
    """Polls until ``stop_polling`` releases it."""

    def __init__(self) -> None:
        super().__init__(failures=0)
        self.started = asyncio.Event()
        self.released = asyncio.Event()

    async def start_polling(self, bot: Any, **kwargs: Any) -> None:
        await super().start_polling(bot, **kwargs)
        self.started.set()
        await self.released.wait()

    async def stop_polling(self) -> None:
        self.stops += 1
        self.released.set()


@pytest.fixture(autouse=True)
def instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the retry tests fast; the delay itself is tested directly."""
    monkeypatch.setattr("app.__main__._retry_delay", lambda attempt: 0.0)


async def test_retries_a_failed_start_then_polls() -> None:
    dp = FlakyDispatcher(failures=2)
    await _poll_forever(dp, object(), asyncio.Event(), retries=5)  # type: ignore[arg-type]
    assert dp.starts == 3


async def test_gives_up_after_the_configured_attempts() -> None:
    dp = FlakyDispatcher(failures=99)
    with pytest.raises(RuntimeError):
        await _poll_forever(dp, object(), asyncio.Event(), retries=3)  # type: ignore[arg-type]
    assert dp.starts == 3


async def test_zero_retries_raises_immediately() -> None:
    dp = FlakyDispatcher(failures=1)
    with pytest.raises(RuntimeError):
        await _poll_forever(dp, object(), asyncio.Event(), retries=0)  # type: ignore[arg-type]
    assert dp.starts == 1


async def test_stop_during_retry_does_not_restart() -> None:
    dp = FlakyDispatcher(failures=5)
    stop = asyncio.Event()
    stop.set()
    await _poll_forever(dp, object(), stop, retries=5)  # type: ignore[arg-type]
    assert dp.starts == 0


async def test_shutdown_stops_the_poller_cleanly() -> None:
    dp = BlockingDispatcher()
    stop = asyncio.Event()
    task = asyncio.create_task(
        _poll_forever(dp, object(), stop, retries=5)  # type: ignore[arg-type]
    )
    await asyncio.wait_for(dp.started.wait(), timeout=2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert dp.starts == 1
    assert dp.stops == 1


def test_retry_delay_grows_then_caps() -> None:
    assert _retry_delay(1) == 5.0
    assert _retry_delay(3) == 15.0
    assert _retry_delay(100) == 60.0


def test_poll_settings_are_bounded() -> None:
    high = Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        poll_start_delay_s=500,
        poll_retries=50,
    )
    low = Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        poll_start_delay_s=-5,
        poll_retries=-1,
    )

    assert high.poll_start_delay_s == 60.0
    assert high.poll_retries == 20
    assert low.poll_start_delay_s == 0.0
    assert low.poll_retries == 0


async def test_health_port_is_served_before_boot_finishes() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    runner = await _start_web(port)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"http://127.0.0.1:{port}/healthz")
    finally:
        await runner.cleanup()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
