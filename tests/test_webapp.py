"""Web surface: the mini app page, its auth, and the API contract."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from app.config import Settings
from app.db.models import User
from app.db.seed import seed_gates
from app.miniapp.auth import sign_init_data, verify_init_data
from app.webapp import build_app, page_html, public_base
from sqlalchemy.ext.asyncio import AsyncSession

TOKEN = "123456:test-token"


def settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        live_only=False,
        tg_token=TOKEN,
        admin_ids=[42],
        **overrides,
    )


def init_data(user_id: int = 7, *, age_s: int = 0, token: str = TOKEN) -> str:
    return sign_init_data(
        {
            "auth_date": str(int(time.time()) - age_s),
            "user": json.dumps({"id": user_id, "username": "tester"}),
        },
        token,
    )


def fake_runtime() -> SimpleNamespace:
    return SimpleNamespace(settings=settings())


@pytest.fixture
def scope(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    @asynccontextmanager
    async def _scope() -> AsyncIterator[AsyncSession]:
        yield session
        await session.commit()

    monkeypatch.setattr("app.miniapp.api.session_scope", _scope)
    monkeypatch.setattr("app.miniapp.api.runtime", fake_runtime)


# --- public_base ------------------------------------------------------------
def test_public_base_prefers_miniapp_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAPP_BASE", "https://smartbin.example.com/")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://other.onrender.com")
    assert public_base() == "https://smartbin.example.com"


def test_public_base_falls_back_to_render_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAPP_BASE", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://smartbin-eurr.onrender.com")
    assert public_base() == "https://smartbin-eurr.onrender.com"


def test_public_base_rejects_non_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAPP_BASE", "http://insecure.example.com")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    assert public_base() == ""


# --- the page ---------------------------------------------------------------
def test_page_is_the_full_mini_app() -> None:
    html = page_html()
    assert "Smart Bin" in html
    assert "Telegram.WebApp" in html
    assert "support-fab" in html
    assert "openSupport" in html
    assert 'id="checkGate"' in html
    assert "/api/kill" in html
    assert "venice" not in html.lower()


async def test_root_serves_the_mini_app() -> None:
    async with TestClient(TestServer(build_app())) as client:
        response = await client.get("/")
        assert response.status == 200
        assert response.content_type == "text/html"
        assert "Smart Bin" in await response.text()


async def test_healthz_stays_json() -> None:
    async with TestClient(TestServer(build_app())) as client:
        response = await client.get("/healthz")
        assert response.status == 200
        assert await response.json() == {"service": "smart-bin", "status": "ok"}


# --- initData ---------------------------------------------------------------
def test_init_data_roundtrip() -> None:
    user = verify_init_data(init_data(7), TOKEN)
    assert user is not None and user["id"] == 7


def test_init_data_rejects_wrong_token() -> None:
    assert verify_init_data(init_data(7), "999:other") is None


def test_init_data_rejects_tampering() -> None:
    payload = init_data(7).replace('"id": 7', '"id": 99')
    assert verify_init_data(payload, TOKEN) is None


def test_init_data_rejects_expired_payload() -> None:
    assert verify_init_data(init_data(7, age_s=90_000), TOKEN) is None


def test_init_data_rejects_empty() -> None:
    assert verify_init_data("", TOKEN) is None
    assert verify_init_data("hash=abc", TOKEN) is None


# --- API --------------------------------------------------------------------
async def test_status_requires_a_valid_payload(scope: None) -> None:
    async with TestClient(TestServer(build_app())) as client:
        assert (await client.get("/api/status")).status == 401
        response = await client.get(
            "/api/status", headers={"X-Telegram-Init-Data": init_data(7, token="bad")}
        )
        assert response.status == 401


async def test_status_returns_user_quota_and_gates(scope: None, session: AsyncSession) -> None:
    await seed_gates(session)
    session.add(User(id=7, credits=Decimal("12")))
    await session.flush()

    async with TestClient(TestServer(build_app())) as client:
        response = await client.get(
            "/api/status", headers={"X-Telegram-Init-Data": init_data(7)}
        )
        payload = await response.json()

    assert response.status == 200
    assert payload["user"]["id"] == 7
    assert payload["user"]["credits"] == 12
    assert payload["quota"]["bin_free"] is True
    assert payload["support_url"].startswith("https://t.me/")
    assert any(gate["id"] == "auth_stripe" for gate in payload["gates"])


async def test_admin_quota_flag(scope: None, session: AsyncSession) -> None:
    session.add(User(id=42, credits=Decimal("0")))
    await session.flush()
    async with TestClient(TestServer(build_app())) as client:
        response = await client.get(
            "/api/status", headers={"X-Telegram-Init-Data": init_data(42)}
        )
        payload = await response.json()
    assert payload["quota"]["admin"] is True


async def test_gates_endpoint_lists_auth_lanes(scope: None, session: AsyncSession) -> None:
    await seed_gates(session)
    async with TestClient(TestServer(build_app())) as client:
        response = await client.get(
            "/api/gates", headers={"X-Telegram-Init-Data": init_data(7)}
        )
        payload = await response.json()
    assert response.status == 200
    assert payload["gates"]
    assert all(gate["lane"] == "auth" for gate in payload["gates"])


async def test_bin_rejects_a_short_number(scope: None) -> None:
    async with TestClient(TestServer(build_app())) as client:
        response = await client.get(
            "/api/bin?bin=123",
            headers={"X-Telegram-Init-Data": init_data(7)},
        )
    assert response.status == 400


async def test_check_rejects_a_bad_card(scope: None) -> None:
    async with TestClient(TestServer(build_app())) as client:
        response = await client.post(
            "/api/check",
            json={"card": "not a card"},
            headers={"X-Telegram-Init-Data": init_data(7)},
        )
    assert response.status == 400


async def test_kill_requires_auth_and_a_valid_card(scope: None) -> None:
    async with TestClient(TestServer(build_app())) as client:
        assert (await client.post("/api/kill", json={"card": "x"})).status == 401
        response = await client.post(
            "/api/kill",
            json={"card": "not a card"},
            headers={"X-Telegram-Init-Data": init_data(8)},
        )
    assert response.status == 400


async def test_check_rate_limits_per_user(scope: None) -> None:
    headers = {"X-Telegram-Init-Data": init_data(7)}
    async with TestClient(TestServer(build_app())) as client:
        statuses = [
            (
                await client.post(
                    "/api/check", json={"card": "nope"}, headers=headers
                )
            ).status
            for _ in range(31)
        ]
    assert statuses[-1] == 429
