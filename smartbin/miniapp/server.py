from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from aiohttp import web

from config import DISPLAY_VERSION
from core.canaries import percentile
from models import utc_date, utcnow_iso

log = logging.getLogger("smartbin.miniapp")

STATIC = Path(__file__).resolve().parent / "static" / "index.html"
BOT_USERNAME_FALLBACK = "SmartBlN_Bot"
_BOT_USERNAME = BOT_USERNAME_FALLBACK


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
        "monthly_users": await db.monthly_users(),
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


def _init_user(pairs: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(pairs.get("user", "") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _touch_user(runtime: Any, pairs: dict[str, Any]) -> None:
    """Record Mini App activity for monthly-active counting."""
    data = _init_user(pairs)
    user_id = data.get("id")
    if not user_id:
        return
    await runtime.db.ensure_user(
        int(user_id),
        username=data.get("username") or "",
        first_name=data.get("first_name") or "",
    )


def _safe_uptime(runtime: Any) -> str:
    try:
        return runtime.uptime()
    except Exception:
        return ""


async def _safe_touch(runtime: Any, pairs: dict[str, Any]) -> None:
    """Activity accounting must never break a Mini App response."""
    try:
        await _touch_user(runtime, pairs)
    except Exception as exc:
        log.warning("event=miniapp_touch_failed error=%s", exc)


def _degraded_status(runtime: Any) -> dict[str, Any]:
    """Minimal status payload so the UI still renders when the DB hiccups."""
    return {
        "uptime": _safe_uptime(runtime),
        "monthly_users": 0,
        "checks_today": 0,
        "cache_depth": 0,
        "p50": 0,
        "p95": 0,
        "revenue_xtr": 0,
        "gates": [],
        "version": DISPLAY_VERSION,
        "date": utc_date(),
        "degraded": True,
    }


async def _bot_username(runtime: Any) -> str:
    global _BOT_USERNAME
    try:
        me = await runtime.bot.get_me()
        username = getattr(me, "username", None)
        if username:
            _BOT_USERNAME = username
    except Exception as exc:
        log.warning("event=miniapp_getme_failed error=%s", exc)
    return _BOT_USERNAME


@web.middleware
async def _no_store(request: web.Request, handler: Any) -> web.StreamResponse:
    """Prevent Telegram/WebView caches from serving a stale or broken shell."""
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        try:
            exc.headers.setdefault("Cache-Control", "no-store")
        except Exception:
            pass
        raise
    except Exception as exc:
        log.exception(
            "event=miniapp_unhandled path=%s error=%s", request.path, exc
        )
        response = web.json_response({"error": "server error"}, status=500)
    try:
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"
    except Exception:
        pass
    return response


def create_app(runtime: Any) -> web.Application:
    app = web.Application(middlewares=[_no_store])
    app["runtime"] = runtime

    async def index(request: web.Request) -> web.StreamResponse:
        try:
            if not STATIC.exists():
                return web.Response(text="Mini App not built", status=404)
            return web.FileResponse(STATIC)
        except Exception as exc:
            log.exception("event=miniapp_index_failed error=%s", exc)
            return web.Response(text="Mini App temporarily unavailable", status=503)

    async def favicon(request: web.Request) -> web.Response:
        return web.Response(status=204)

    async def api_bin(request: web.Request) -> web.Response:
        try:
            pairs = verify_init_data(_init_data(request), runtime.config.bot_token)
        except Exception as exc:
            log.warning("event=miniapp_verify_failed error=%s", exc)
            pairs = None
        if pairs is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        await _safe_touch(runtime, pairs)

        raw = request.query.get("bin", "")
        digits = "".join(c for c in raw if c.isdigit())
        if len(digits) not in (6, 8):
            return web.json_response({"error": "bin must be 6 or 8 digits"}, status=400)
        try:
            if len(digits) == 6:
                record = await runtime.store.get_prefix(digits)
            else:
                record = await runtime.store.get(digits)
        except Exception as exc:
            log.warning("event=miniapp_bin_failed bin=%s error=%s", digits, exc)
            return web.json_response({"error": "lookup unavailable"}, status=503)
        if record is None:
            return web.json_response({"error": "BIN not found"}, status=404)
        try:
            payload = card_json(record, free=True)
        except Exception as exc:
            log.warning("event=miniapp_card_failed bin=%s error=%s", digits, exc)
            return web.json_response({"error": "lookup unavailable"}, status=503)
        return web.json_response(payload)

    async def api_status(request: web.Request) -> web.Response:
        try:
            pairs = verify_init_data(_init_data(request), runtime.config.bot_token)
        except Exception as exc:
            log.warning("event=miniapp_verify_failed error=%s", exc)
            pairs = None
        if pairs is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        await _safe_touch(runtime, pairs)
        try:
            payload = await status_json(runtime)
        except Exception as exc:
            log.warning("event=miniapp_status_failed error=%s", exc)
            payload = _degraded_status(runtime)
        return web.json_response(payload)

    async def api_reflink(request: web.Request) -> web.Response:
        try:
            pairs = verify_init_data(_init_data(request), runtime.config.bot_token)
        except Exception as exc:
            log.warning("event=miniapp_verify_failed error=%s", exc)
            pairs = None
        if pairs is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        await _safe_touch(runtime, pairs)
        user_id = request.query.get("user_id", "")
        bot_username = await _bot_username(runtime)
        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}" if user_id else ""
        return web.json_response({"ref_link": ref_link})

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "version": DISPLAY_VERSION, "time": utcnow_iso()})

    app.router.add_get("/", index)
    app.router.add_get("/favicon.ico", favicon)
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
