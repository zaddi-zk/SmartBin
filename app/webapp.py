"""Public web surface: the Telegram mini app and the health endpoint.

The page is the platform's own mini app. Its API is implemented in
``app.miniapp.api`` on top of the same runtime the bot uses, so a check from
the web and a check from Telegram share gates, keys, proxies and the ledger.
"""

from __future__ import annotations

import os
from pathlib import Path

from aiohttp import web

from app.miniapp import api

SERVICE_NAME = "smart-bin"
INDEX_PATH = Path(__file__).parent / "miniapp" / "index.html"

# The menu button can only open https URLs. Render supplies RENDER_EXTERNAL_URL
# automatically; MINIAPP_BASE wins when an operator pins a custom domain.
_BASE_ENV = ("MINIAPP_BASE", "RENDER_EXTERNAL_URL")


def public_base() -> str:
    """The https origin the menu button should open, or ``""`` when unknown."""
    for name in _BASE_ENV:
        raw = os.environ.get(name, "").strip().rstrip("/")
        if raw.startswith("https://"):
            return raw
    return ""


def page_html() -> str:
    return INDEX_PATH.read_text(encoding="utf-8")


def build_app() -> web.Application:
    app = web.Application()
    html = page_html()

    async def index(_: web.Request) -> web.Response:
        return web.Response(text=html, content_type="text/html")

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"service": SERVICE_NAME, "status": "ok"})

    async def favicon(_: web.Request) -> web.Response:
        return web.Response(status=204)

    # `/` is the menu-button target, so it must render the app, not JSON.
    # `/healthz` stays machine-readable for uptime monitors.
    app.router.add_get("/", index)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_get("/healthz", health)
    api.register(app)
    return app
