"""Public web surface: the page the Telegram menu button opens.

The platform has no client-side API yet, so this page is deliberately static:
it names the product and hands the user back to the bot. Mounting the legacy
mini-app here would point the platform at the legacy Supabase schema, which is
explicitly out of bounds.
"""

from __future__ import annotations

import os

from aiohttp import web

SERVICE_NAME = "smart-bin"
BOT_USERNAME = "SmartBlN_Bot"
BOT_URL = f"https://t.me/{BOT_USERNAME}"

# The menu button can only open https URLs. Render supplies RENDER_EXTERNAL_URL
# automatically; MINIAPP_BASE wins when an operator pins a custom domain.
_BASE_ENV = ("MINIAPP_BASE", "RENDER_EXTERNAL_URL")

LANDING_HTML = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Smart Bin</title>
<style>
  :root {{
    color-scheme: dark;
    --bg: #0b0f14;
    --card: #121821;
    --line: #1e2733;
    --ink: #e8eef6;
    --muted: #8a97a8;
    --accent: #4ade80;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100vh;
    display: grid;
    place-items: center;
    padding: 24px;
    background: radial-gradient(120% 80% at 50% 0%, #16202c 0%, var(--bg) 60%);
    color: var(--ink);
    font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
  }}
  main {{
    width: 100%;
    max-width: 420px;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 20px;
    padding: 28px 24px;
    text-align: center;
    box-shadow: 0 24px 60px rgba(0, 0, 0, .45);
  }}
  .mark {{ font-size: 40px; line-height: 1; }}
  h1 {{ margin: 12px 0 4px; font-size: 26px; letter-spacing: -.02em; }}
  .tag {{ margin: 0 0 22px; color: var(--muted); font-size: 14px; }}
  ul {{
    list-style: none;
    margin: 0 0 24px;
    padding: 16px;
    background: #0e141c;
    border: 1px solid var(--line);
    border-radius: 14px;
    text-align: left;
    font-size: 14px;
  }}
  li {{ display: flex; gap: 10px; padding: 6px 0; }}
  li + li {{ border-top: 1px solid var(--line); }}
  li span:last-child {{ color: var(--muted); }}
  a.button {{
    display: block;
    padding: 14px 18px;
    border-radius: 12px;
    background: var(--accent);
    color: #06210f;
    font-weight: 700;
    text-decoration: none;
  }}
  a.button:active {{ transform: scale(.99); }}
  footer {{ margin-top: 18px; color: var(--muted); font-size: 12px; }}
</style>
</head>
<body>
<main>
  <div class="mark">🧬</div>
  <h1>Smart Bin</h1>
  <p class="tag">Card intelligence, verified.</p>
  <ul>
    <li><span>🔍</span><span>2-source BIN lookup</span></li>
    <li><span>🛡️</span><span>Routes the best 3DS auth gate</span></li>
    <li><span>💸</span><span>No charge when a check fails</span></li>
  </ul>
  <a class="button" href="{BOT_URL}">Open @{BOT_USERNAME}</a>
  <footer>SMARTBIN</footer>
</main>
</body>
</html>
"""


def public_base() -> str:
    """The https origin the menu button should open, or ``""`` when unknown."""
    for name in _BASE_ENV:
        raw = os.environ.get(name, "").strip().rstrip("/")
        if raw.startswith("https://"):
            return raw
    return ""


def build_app() -> web.Application:
    app = web.Application()

    async def landing(_: web.Request) -> web.Response:
        return web.Response(text=LANDING_HTML, content_type="text/html")

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"service": SERVICE_NAME, "status": "ok"})

    # `/` is the menu-button target, so it must render a page, not JSON.
    # `/healthz` stays machine-readable for uptime monitors.
    app.router.add_get("/", landing)
    app.router.add_get("/healthz", health)
    return app
