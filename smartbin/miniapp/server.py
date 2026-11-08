from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from aiohttp import web

from config import DISPLAY_VERSION
from core.canaries import percentile
from models import utc_date, utcnow_iso

STATIC = Path(__file__).resolve().parent / "static" / "index.html"


def verify_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict[str, Any] | None:
    """Telegram WebApp HMAC-SHA256 check. Unsigned or stale data returns None."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except ValueError:
        return None
    received = pairs.pop("hash", "")
    if not received:
        return None
    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received):
        return None
    if max_age:
        try:
            auth_date = int(pairs.get("auth_date", "0"))
        except ValueError:
            return None
        if auth_date and (datetime.now(timezone.utc).timestamp() - auth_date) > max_age:
            return None
    return pairs


def _json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def card_json(record: Any, free: bool = True) -> dict[str, Any]:
    sources = _json(record.sources_json)
    confs = _json(record.field_conf_json)
    return {
        "bin": record.bin,
        "brand": record.brand,
        "level": record.level,
        "bank": record.bank,
        "country": record.country,
        "iso2": record.iso2,
        "currency": record.currency,
        "phone": record.phone,
        "site": record.site,
        "prepaid": int(record.prepaid),
        "commercial": int(record.commercial),
        "avs": int(record.avs),
        "card_length": int(record.card_length),
        "luhn": int(record.luhn),
        "bin_age": int(record.bin_age),
        "sample_size": int(record.sample_size),
        "verified_utc": record.verified_utc,
        "confidence": record.confidence,
        "field_conf": confs,
        "sources": list(sources.keys()),
        "twofa": record.twofa,
        "twofa_conf": record.twofa_conf,
        "twofa_reads": record.twofa_reads,
        "watermark": free,
        "version": DISPLAY_VERSION,
        "date": utc_date(),
    }


async def status_json(runtime: Any) -> dict[str, Any]:
    db = runtime.db
    stats = await db.stats_all()
    latencies = [
        float(r["latency_ms"])
        for r in await db.fetchall(
            "SELECT latency_ms FROM checks WHERE latency_ms>0 ORDER BY id DESC LIMIT 200"
        )
    ]
    gates = []
    for index, gate in enumerate(await db.list_gates(), start=1):
        info = runtime.canary.stats(gate)
        info["label"] = f"gate-{index}"
        gates.append(info)
    revenue = int(
        await db.scalar(
            "SELECT COALESCE(SUM(amount_xtr),0) FROM receipts WHERE state='paid'", (), 0
        )
    )
    return {
        "uptime": runtime.uptime(),
        "users": await db.count("users"),
        "checks_today": int(stats.get(f"day:{utc_date()}:checks", 0)),
        "cache_depth": runtime.store.depth(),
        "p50": int(percentile(latencies, 50)),
        "p95": int(percentile(latencies, 95)),
        "revenue_xtr": revenue,
        "gates": gates,
        "version": DISPLAY_VERSION,
        "date": utc_date(),
    }


def _init_data(request: web.Request) -> str:
    header = request.headers.get("X-Telegram-Init-Data", "")
    return header or request.query.get("init_data", "")


def create_app(runtime: Any) -> web.Application:
    app = web.Application()
    app["runtime"] = runtime

    async def index(request: web.Request) -> web.StreamResponse:
        if not STATIC.exists():
            return web.Response(text="Mini App not built", status=404)
        return web.FileResponse(STATIC)

    async def api_bin(request: web.Request) -> web.Response:
        if verify_init_data(_init_data(request), runtime.config.bot_token) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        raw = request.query.get("bin", "")
        digits = "".join(c for c in raw if c.isdigit())
        if len(digits) not in (6, 8):
            return web.json_response({"error": "bin must be 6 or 8 digits"}, status=400)
        record = await runtime.store.get_prefix(digits) if len(digits) == 6 else await runtime.store.get(digits)
        return web.json_response(card_json(record, free=True))

    async def api_status(request: web.Request) -> web.Response:
        if verify_init_data(_init_data(request), runtime.config.bot_token) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(await status_json(runtime))

    async def api_reflink(request: web.Request) -> web.Response:
        if verify_init_data(_init_data(request), runtime.config.bot_token) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        user_id = request.query.get("user_id", "")
        bot_username = (await runtime.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}" if user_id else ""
        return web.json_response({"ref_link": ref_link})

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "version": DISPLAY_VERSION, "time": utcnow_iso()})

    app.router.add_get("/", index)
    app.router.add_get("/api/bin", api_bin)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/status", api_status)
    app.router.add_get("/api/reflink", api_reflink)
    app.router.add_get("/api/health", health)
    return app


async def start_server(runtime: Any, host: str = "0.0.0.0", port: int | None = None) -> web.AppRunner:
    app = create_app(runtime)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port if port is not None else runtime.config.miniapp_port)
    await site.start()
    return runner


def server_port(runner: web.AppRunner) -> int:
    try:
        site = runner.addresses[0]
        return int(site[1])
    except (IndexError, TypeError, ValueError):
        return 0
