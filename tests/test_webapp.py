from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from app.webapp import LANDING_HTML, build_app, public_base


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


def test_landing_page_is_branded() -> None:
    assert "<title>Smart Bin</title>" in LANDING_HTML
    assert "Smart Bin" in LANDING_HTML
    assert "https://t.me/SmartBlN_Bot" in LANDING_HTML
    assert "venice" not in LANDING_HTML.lower()


async def test_root_serves_the_branded_page() -> None:
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
