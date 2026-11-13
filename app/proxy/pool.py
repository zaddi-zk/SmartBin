"""Proxy pool + sticky country binding (section 13).

A key is bound to one live proxy in its country for its lifetime. The pool is
DB-backed: ``px:add`` (admin) or the free batch fills it, and every proxy is
tested through ``api.ipify.org`` before it is served.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.db.models import ApiKey, Proxy, User
from app.providers.base import Key

IPIFY = "https://api.ipify.org?format=json"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ProxyCheck:
    url: str
    alive: bool
    latency_ms: int
    ip: str = ""


class ProxyPool:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def test(self, url: str, timeout_s: float = 8.0) -> ProxyCheck:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(proxy=url, timeout=timeout_s) as client:
                response = await client.get(IPIFY)
            elapsed = int((time.perf_counter() - started) * 1000)
            if response.status_code != 200:
                return ProxyCheck(url=url, alive=False, latency_ms=elapsed)
            payload = response.json()
            ip = str(payload.get("ip", "")) if isinstance(payload, dict) else ""
            return ProxyCheck(url=url, alive=True, latency_ms=elapsed, ip=ip)
        except (httpx.HTTPError, ValueError):
            elapsed = int((time.perf_counter() - started) * 1000)
            return ProxyCheck(url=url, alive=False, latency_ms=elapsed)

    async def bind(self, session: AsyncSession, key_row: ApiKey, key: Key) -> str | None:
        """Pin a country-matched proxy onto the key and return its URL."""
        if key.proxy_id is not None:
            proxy = await session.get(Proxy, key.proxy_id)
            if proxy is not None and proxy.alive:
                return proxy.url

        candidates = await repo.alive_proxies(session, key.country)
        if not candidates:
            candidates = await repo.alive_proxies(session)
        if not candidates:
            return None

        chosen = candidates[key.id % len(candidates)]
        key_row.proxy_id = chosen.id
        return chosen.url

    async def free_batch(self, session: AsyncSession, user: User, limit: int = 10) -> list[Proxy]:
        """Ten checked proxies per 24h, per user."""
        now = _now()
        if user.free_proxy_at is not None and now - user.free_proxy_at < timedelta(
            seconds=self.settings.free_proxy_cooldown_s
        ):
            return []
        user.free_proxy_at = now
        return (await repo.alive_proxies(session))[:limit]
