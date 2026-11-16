"""Proxy pool + sticky country binding (section 13).

A key is bound to one live proxy in its country for its lifetime. The pool is
DB-backed: admin import or the seed file fills it, ``refresh`` tests every row
through ``api.ipify.org``, and only recently checked, low-latency rows are
served. Untested rows are never handed to a card check.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import Float, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ApiKey, Proxy, User
from app.providers.base import Key
from app.proxy.importer import parse_proxy_text

IPIFY = "https://api.ipify.org?format=json"


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except OSError:
        return ""


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ProxyCheck:
    url: str
    alive: bool
    latency_ms: int
    ip: str = ""


@dataclass(slots=True)
class ProxyStats:
    total: int = 0
    alive: int = 0
    tested: int = 0
    users_without: int = 0
    fastest_ms: int | None = None


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

    async def test_many(
        self, urls: list[str], concurrency: int = 20
    ) -> list[ProxyCheck]:
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def one(url: str) -> ProxyCheck:
            async with semaphore:
                return await self.test(url)

        return list(await asyncio.gather(*(one(url) for url in urls)))

    async def import_many(
        self, session: AsyncSession, text: str, country: str = ""
    ) -> int:
        """Insert every new proxy in a dump. Returns the number added."""
        urls = parse_proxy_text(text)
        if not urls:
            return 0
        existing = set((await session.scalars(select(Proxy.url))).all())
        added = 0
        for url in urls:
            if url in existing:
                continue
            existing.add(url)
            session.add(Proxy(url=url, country=country.upper()[:2], alive=True))
            added += 1
        await session.flush()
        return added

    async def seed_from_env(self, session: AsyncSession) -> int:
        """Load the configured seed file or URL, if any. Idempotent."""
        text = ""
        path = self.settings.proxy_seed_file.strip()
        if path:
            text = await asyncio.to_thread(_read_text, path)
        url = self.settings.proxy_seed_url.strip()
        if not text and url:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.get(url)
                if response.status_code == 200:
                    text = response.text
            except httpx.HTTPError:
                text = ""
        return await self.import_many(session, text) if text else 0

    async def refresh(self, session: AsyncSession, limit: int = 200) -> tuple[int, int]:
        """Test the least-recently-checked rows. Returns (alive, tested)."""
        stmt = (
            select(Proxy)
            .order_by(Proxy.checked_at.asc().nulls_first())
            .limit(limit)
        )
        rows = list((await session.scalars(stmt)).all())
        if not rows:
            return 0, 0
        checks = await self.test_many([row.url for row in rows])
        alive = 0
        now = _now()
        for row, check in zip(rows, checks, strict=True):
            row.alive = check.alive
            row.latency_ms = check.latency_ms
            row.checked_at = now
            if check.alive:
                row.ok_count = (row.ok_count or 0) + 1
                alive += 1
            else:
                row.fail_count = (row.fail_count or 0) + 1
        await session.flush()
        return alive, len(rows)

    async def ranked(
        self, session: AsyncSession, country: str = "", limit: int = 10
    ) -> list[Proxy]:
        """Recently checked, alive, under the latency cap, fastest first."""
        cutoff = _now() - timedelta(hours=self.settings.proxy_refresh_hours)
        stmt = select(Proxy).where(
            Proxy.alive.is_(True),
            Proxy.checked_at.is_not(None),
            Proxy.checked_at >= cutoff,
            Proxy.latency_ms.is_not(None),
            Proxy.latency_ms <= self.settings.proxy_max_latency_ms,
        )
        if country:
            stmt = stmt.where(Proxy.country == country)
        # Stability first: a proxy that answered every recent probe beats a
        # marginally faster one that keeps dropping. Latency breaks the tie.
        stability = cast(Proxy.ok_count + 1, Float) / (
            Proxy.ok_count + Proxy.fail_count + 1
        )
        stmt = stmt.order_by(stability.desc(), Proxy.latency_ms.asc()).limit(limit)
        return list((await session.scalars(stmt)).all())

    async def bind(self, session: AsyncSession, key_row: ApiKey, key: Key) -> str | None:
        """Pin a country-matched proxy onto the key and return its URL."""
        cutoff = _now() - timedelta(hours=self.settings.proxy_refresh_hours)
        if key.proxy_id is not None:
            proxy = await session.get(Proxy, key.proxy_id)
            if (
                proxy is not None
                and proxy.alive
                and proxy.checked_at is not None
                and proxy.checked_at >= cutoff
            ):
                return proxy.url

        candidates = await self.ranked(session, key.country)
        if not candidates:
            candidates = await self.ranked(session)
        if not candidates:
            return None

        chosen = candidates[key.id % len(candidates)]
        key_row.proxy_id = chosen.id
        return chosen.url

    async def free_batch(
        self, session: AsyncSession, user: User, limit: int = 10
    ) -> list[Proxy]:
        """Ten checked proxies per 24h, per user."""
        now = _now()
        if user.free_proxy_at is not None and now - user.free_proxy_at < timedelta(
            seconds=self.settings.free_proxy_cooldown_s
        ):
            return []
        user.free_proxy_at = now
        return await self.ranked(session, limit=limit)

    async def auto_assign(self, session: AsyncSession) -> int:
        """Give every user without live egress a fast proxy, round-robin.

        A user is only touched when their proxy is missing or no longer alive,
        so a healthy assignment is never churned by a later tick.
        """
        pool = await self.ranked(session, limit=10)
        if not pool:
            return 0
        alive_ids = set(
            (await session.scalars(select(Proxy.id).where(Proxy.alive.is_(True)))).all()
        )
        users = list((await session.scalars(select(User))).all())
        changed = 0
        for index, user in enumerate(users):
            if user.proxy_id is not None and user.proxy_id in alive_ids:
                continue
            user.proxy_id = pool[index % len(pool)].id
            changed += 1
        await session.flush()
        return changed

    async def purge_dead(self, session: AsyncSession) -> int:
        """Delete dead rows and detach every user/key that still points at one."""
        dead = list(
            (await session.scalars(select(Proxy).where(Proxy.alive.is_(False)))).all()
        )
        if not dead:
            return 0
        ids = [row.id for row in dead]
        await session.execute(
            update(User).where(User.proxy_id.in_(ids)).values(proxy_id=None)
        )
        await session.execute(
            update(ApiKey).where(ApiKey.proxy_id.in_(ids)).values(proxy_id=None)
        )
        for row in dead:
            await session.delete(row)
        await session.flush()
        return len(dead)

    async def stats(self, session: AsyncSession) -> ProxyStats:
        """Pool health for the admin screen."""
        total = await session.scalar(select(func.count()).select_from(Proxy)) or 0
        alive = (
            await session.scalar(
                select(func.count()).select_from(Proxy).where(Proxy.alive.is_(True))
            )
            or 0
        )
        tested = (
            await session.scalar(
                select(func.count()).select_from(Proxy).where(Proxy.checked_at.is_not(None))
            )
            or 0
        )
        users_without = (
            await session.scalar(
                select(func.count()).select_from(User).where(User.proxy_id.is_(None))
            )
            or 0
        )
        top = await self.ranked(session, limit=1)
        return ProxyStats(
            total=int(total),
            alive=int(alive),
            tested=int(tested),
            users_without=int(users_without),
            fastest_ms=top[0].latency_ms if top else None,
        )

