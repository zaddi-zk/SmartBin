"""Proxy pool + importer (section 13, 24).

The pool must never serve an untested, stale, dead or slow row to a card check.
These tests pin that contract down without touching the network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.config import Settings
from app.db.models import ApiKey, Proxy, User
from app.providers.base import Key
from app.proxy.importer import parse_proxy_line, parse_proxy_text
from app.proxy.pool import ProxyCheck, ProxyPool
from sqlalchemy.ext.asyncio import AsyncSession


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, live_only=False, tg_token="", **overrides)


def pool(**overrides: object) -> ProxyPool:
    return ProxyPool(settings(**overrides))


def now() -> datetime:
    return datetime.now(UTC)


# --- importer ---------------------------------------------------------------
def test_parse_host_port() -> None:
    assert parse_proxy_line("38.1.2.3:8080") == "http://38.1.2.3:8080"


def test_parse_host_port_user_pass() -> None:
    assert (
        parse_proxy_line("181.215.16.247:12345:tickets:proxyon145")
        == "http://tickets:proxyon145@181.215.16.247:12345"
    )


def test_parse_user_pass_at_host() -> None:
    assert (
        parse_proxy_line("user:pass@38.1.2.3:8080") == "http://user:pass@38.1.2.3:8080"
    )


def test_parse_scheme_urls_are_kept() -> None:
    assert parse_proxy_line("socks5://user:pass@1.2.3.4:1080") == "socks5://user:pass@1.2.3.4:1080"
    assert parse_proxy_line("https://1.2.3.4:443") == "https://1.2.3.4:443"


def test_parse_rejects_non_proxies() -> None:
    for raw in (
        "",
        "   ",
        "# comment",
        "not a proxy",
        "1.2.3.4",
        "1.2.3.4:notaport",
        "1.2.3.4:99999",
        "ftp://1.2.3.4:21",
        "1.2.3.4:8080:user",
    ):
        assert parse_proxy_line(raw) is None


def test_parse_text_dedupes_and_keeps_order() -> None:
    text = "1.1.1.1:80\n\n2.2.2.2:81\n1.1.1.1:80\n# x\nbad\n3.3.3.3:82\n"
    assert parse_proxy_text(text) == [
        "http://1.1.1.1:80",
        "http://2.2.2.2:81",
        "http://3.3.3.3:82",
    ]


# --- pool -------------------------------------------------------------------
async def test_import_many_is_idempotent(session: AsyncSession) -> None:
    pool_ = pool()
    assert await pool_.import_many(session, "1.1.1.1:80\n2.2.2.2:81") == 2
    assert await pool_.import_many(session, "1.1.1.1:80\n2.2.2.2:81\n3.3.3.3:82") == 1


async def test_ranked_excludes_untested_dead_stale_and_slow(session: AsyncSession) -> None:
    fresh = now() - timedelta(minutes=5)
    session.add_all(
        [
            Proxy(url="http://fast:1", alive=True, latency_ms=100, checked_at=fresh),
            Proxy(url="http://slow:2", alive=True, latency_ms=9000, checked_at=fresh),
            Proxy(url="http://dead:3", alive=False, latency_ms=50, checked_at=fresh),
            Proxy(url="http://untested:4", alive=True, latency_ms=None, checked_at=None),
            Proxy(
                url="http://stale:5",
                alive=True,
                latency_ms=60,
                checked_at=now() - timedelta(hours=48),
            ),
        ]
    )
    await session.flush()

    rows = await pool().ranked(session)
    assert [row.url for row in rows] == ["http://fast:1"]


async def test_ranked_prefers_stability_over_a_slightly_faster_flaky_row(
    session: AsyncSession,
) -> None:
    fresh = now() - timedelta(minutes=5)
    session.add_all(
        [
            Proxy(
                url="http://flaky:1",
                alive=True,
                latency_ms=80,
                checked_at=fresh,
                ok_count=1,
                fail_count=5,
            ),
            Proxy(
                url="http://stable:2",
                alive=True,
                latency_ms=120,
                checked_at=fresh,
                ok_count=6,
                fail_count=0,
            ),
        ]
    )
    await session.flush()

    rows = await pool().ranked(session)
    assert [row.url for row in rows] == ["http://stable:2", "http://flaky:1"]


async def test_refresh_records_success_and_failure_streaks(session: AsyncSession) -> None:
    good = Proxy(url="http://good:1")
    bad = Proxy(url="http://bad:2")
    session.add_all([good, bad])
    await session.flush()

    async def fake_test_many(urls: list[str], concurrency: int = 20) -> list[ProxyCheck]:
        return [
            ProxyCheck(url=url, alive=url.endswith("1"), latency_ms=50) for url in urls
        ]

    pool_ = pool()
    pool_.test_many = fake_test_many  # type: ignore[method-assign]
    alive, tested = await pool_.refresh(session)

    assert (alive, tested) == (1, 2)
    await session.refresh(good)
    await session.refresh(bad)
    assert (good.ok_count, good.fail_count) == (1, 0)
    assert (bad.ok_count, bad.fail_count) == (0, 1)


async def test_ranked_prefers_country_then_falls_back(session: AsyncSession) -> None:
    fresh = now() - timedelta(minutes=5)
    session.add_all(
        [
            Proxy(url="http://us:1", country="US", alive=True, latency_ms=200, checked_at=fresh),
            Proxy(url="http://de:2", country="DE", alive=True, latency_ms=90, checked_at=fresh),
        ]
    )
    await session.flush()

    us = await pool().ranked(session, "US")
    assert [row.url for row in us] == ["http://us:1"]


async def test_auto_assign_fills_missing_and_repairs_dead(session: AsyncSession) -> None:
    fresh = now() - timedelta(minutes=5)
    live = Proxy(url="http://live:1", alive=True, latency_ms=120, checked_at=fresh)
    dead = Proxy(url="http://dead:2", alive=False, latency_ms=5000, checked_at=fresh)
    session.add_all([live, dead])
    await session.flush()

    healthy = User(id=1, credits=Decimal("0"), proxy_id=live.id)
    missing = User(id=2, credits=Decimal("0"), proxy_id=None)
    broken = User(id=3, credits=Decimal("0"), proxy_id=dead.id)
    session.add_all([healthy, missing, broken])
    await session.flush()

    assert await pool().auto_assign(session) == 2
    assert healthy.proxy_id == live.id
    assert missing.proxy_id == live.id
    assert broken.proxy_id == live.id

    # A second pass is a no-op: healthy assignments are never churned.
    assert await pool().auto_assign(session) == 0


async def test_auto_assign_without_pool_is_a_noop(session: AsyncSession) -> None:
    session.add(User(id=1, credits=Decimal("0"), proxy_id=None))
    await session.flush()
    assert await pool().auto_assign(session) == 0


async def test_purge_dead_detaches_users_and_keys(session: AsyncSession) -> None:
    dead = Proxy(url="http://dead:1", alive=False, latency_ms=10, checked_at=now())
    session.add(dead)
    await session.flush()
    user = User(id=1, credits=Decimal("0"), proxy_id=dead.id)
    key = ApiKey(gate_id="auth_stripe", secret="sk_live_x", state="READY", proxy_id=dead.id)
    session.add_all([user, key])
    await session.flush()

    assert await pool().purge_dead(session) == 1
    await session.flush()

    assert user.proxy_id is None
    assert key.proxy_id is None
    assert await session.get(Proxy, dead.id) is None


async def test_bind_pins_country_matched_fresh_proxy(session: AsyncSession) -> None:
    fresh = now() - timedelta(minutes=5)
    us = Proxy(url="http://us:1", country="US", alive=True, latency_ms=200, checked_at=fresh)
    de = Proxy(url="http://de:2", country="DE", alive=True, latency_ms=90, checked_at=fresh)
    session.add_all([us, de])
    await session.flush()

    row = ApiKey(gate_id="auth_stripe", secret="sk_live_x", state="READY")
    session.add(row)
    await session.flush()

    key = Key(id=row.id, gate_id=row.gate_id, secret=row.secret, country="US")
    assert await pool().bind(session, row, key) == "http://us:1"
    assert row.proxy_id == us.id


async def test_bind_replaces_stale_key_proxy(session: AsyncSession) -> None:
    fresh = now() - timedelta(minutes=5)
    stale = Proxy(
        url="http://stale:1",
        country="US",
        alive=True,
        latency_ms=50,
        checked_at=now() - timedelta(days=3),
    )
    fresh_row = Proxy(
        url="http://fresh:2", country="US", alive=True, latency_ms=150, checked_at=fresh
    )
    session.add_all([stale, fresh_row])
    await session.flush()

    row = ApiKey(
        gate_id="auth_stripe", secret="sk_live_x", state="READY", proxy_id=stale.id
    )
    session.add(row)
    await session.flush()

    key = Key(
        id=row.id, gate_id=row.gate_id, secret=row.secret, country="US", proxy_id=stale.id
    )
    assert await pool().bind(session, row, key) == "http://fresh:2"


async def test_stats_reports_pool_and_user_gaps(session: AsyncSession) -> None:
    fresh = now() - timedelta(minutes=5)
    session.add_all(
        [
            Proxy(url="http://a:1", alive=True, latency_ms=100, checked_at=fresh),
            Proxy(url="http://b:2", alive=False, latency_ms=None, checked_at=None),
            User(id=1, credits=Decimal("0"), proxy_id=None),
        ]
    )
    await session.flush()

    stats = await pool().stats(session)
    assert stats.total == 2
    assert stats.alive == 1
    assert stats.tested == 1
    assert stats.users_without == 1
    assert stats.fastest_ms == 100
