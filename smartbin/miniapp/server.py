from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from aiohttp import web

from config import DISPLAY_VERSION
from core.canaries import percentile
from models import utc_date, utcnow_iso
from quota import (
    claim_usage,
    is_admin,
    is_banned,
    is_premium,
    quota_snapshot,
    refund_usage,
    should_refund,
)

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


def _auth_pairs(request: web.Request, runtime: Any) -> dict[str, Any] | None:
    """Verify Telegram initData and log *why* a request was rejected."""
    raw = _init_data(request)
    try:
        pairs = verify_init_data(raw, runtime.config.bot_token)
    except Exception as exc:
        log.warning("event=miniapp_verify_failed error=%s", exc)
        return None
    if pairs is None:
        log.warning(
            "event=miniapp_unauthorized path=%s has_init_data=%s bot_token_set=%s",
            request.path,
            bool(raw),
            bool(runtime.config.bot_token),
        )
    return pairs


def _init_user(pairs: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(pairs.get("user", "") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _touch_user(runtime: Any, pairs: dict[str, Any]) -> Any:
    """Record activity and return the fresh user row with today's quota."""
    data = _init_user(pairs)
    try:
        user_id = int(data.get("id") or 0)
    except (TypeError, ValueError):
        return None
    if not user_id:
        return None
    await runtime.db.ensure_user(
        user_id,
        username=data.get("username") or "",
        first_name=data.get("first_name") or "",
    )
    return await runtime.db.reset_quota_if_needed(user_id)


def _safe_uptime(runtime: Any) -> str:
    try:
        return runtime.uptime()
    except Exception:
        return ""


async def _safe_touch(runtime: Any, pairs: dict[str, Any]) -> Any:
    """Activity accounting must never break a Mini App response."""
    try:
        return await _touch_user(runtime, pairs)
    except Exception as exc:
        log.warning("event=miniapp_touch_failed error=%s", exc)
        return None


def _limit_response(user: Any, runtime: Any, kind: str) -> web.Response:
    if is_banned(user):
        return web.json_response(
            {
                "error": "Account restricted. Contact support.",
                "code": "banned",
                "kind": kind,
                "quota": quota_snapshot(user, runtime.config),
            },
            status=403,
        )
    return web.json_response(
        {
            "error": "Daily free limit reached. Buy credits or premium to continue.",
            "code": "limit_reached",
            "kind": kind,
            "quota": quota_snapshot(user, runtime.config),
        },
        status=402,
    )


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
async def _cors_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    """CORS and cache control middleware for Mini App."""
    # Set CORS headers
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
        "Access-Control-Max-Age": "86400",
    }
    
    try:
        # Handle preflight
        if request.method == "OPTIONS":
            return web.Response(headers=cors_headers)
        
        response = await handler(request)
    except web.HTTPException as exc:
        try:
            exc.headers.update(cors_headers)
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
        # Cache control
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # CORS
        response.headers.update(cors_headers)
    except Exception:
        pass
    return response


def create_app(runtime: Any) -> web.Application:
    app = web.Application(middlewares=[_cors_middleware])
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
        pairs = _auth_pairs(request, runtime)
        if pairs is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        user = await _safe_touch(runtime, pairs)
        if user is None:
            return web.json_response({"error": "user not found"}, status=401)

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
            # A miss costs nothing.
            return web.json_response({"error": "BIN not found"}, status=404)

        decision = await claim_usage(runtime.db, user, "bin", runtime.config)
        if not decision["allowed"]:
            return _limit_response(user, runtime, "bin")

        fresh = await runtime.db.get_user(int(user["user_id"])) or user
        try:
            payload = card_json(
                record,
                free=not (is_premium(fresh) or is_admin(int(fresh["user_id"]), runtime.config)),
            )
        except Exception as exc:
            await refund_usage(runtime.db, int(fresh["user_id"]), "bin", decision["consume"])
            log.warning("event=miniapp_card_failed bin=%s error=%s", digits, exc)
            return web.json_response({"error": "lookup unavailable"}, status=503)
        payload["quota"] = quota_snapshot(fresh, runtime.config)
        return web.json_response(payload)

    async def api_status(request: web.Request) -> web.Response:
        pairs = _auth_pairs(request, runtime)
        if pairs is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        user = await _safe_touch(runtime, pairs)
        if user is None:
            return web.json_response({"error": "user not found"}, status=401)
        try:
            payload = await status_json(runtime)
        except Exception as exc:
            log.warning("event=miniapp_status_failed error=%s", exc)
            payload = _degraded_status(runtime)
        payload["quota"] = quota_snapshot(user, runtime.config)
        payload["user"] = {
            "id": int(user["user_id"]),
            "credits": int(user["credits"] or 0),
            "premium": is_premium(user),
        }
        return web.json_response(payload)

    async def api_reflink(request: web.Request) -> web.Response:
        pairs = _auth_pairs(request, runtime)
        if pairs is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        await _safe_touch(runtime, pairs)
        user_id = request.query.get("user_id", "")
        bot_username = await _bot_username(runtime)
        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}" if user_id else ""
        return web.json_response({"ref_link": ref_link})

    async def api_check(request: web.Request) -> web.Response:
        """Card check endpoint for Mini App."""
        pairs = _auth_pairs(request, runtime)
        if pairs is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        user = await _safe_touch(runtime, pairs)
        if user is None:
            return web.json_response({"error": "user not found"}, status=401)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        card_raw = str(body.get("card", "")).strip()
        if not card_raw or "|" not in card_raw:
            return web.json_response({"error": "invalid card format"}, status=400)

        from models import Card
        try:
            card = Card.parse(card_raw)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        uid = int(user["user_id"])
        # Same rule as the bot: 2 free checks per day, then credits.
        decision = await claim_usage(runtime.db, user, "check", runtime.config)
        if not decision["allowed"]:
            return _limit_response(user, runtime, "check")

        try:
            outcome = await runtime.runner.run(uid, card_raw, purpose="check")
        except Exception as exc:
            await refund_usage(runtime.db, uid, "check", decision["consume"])
            log.warning("event=miniapp_check_failed card=%s error=%s", card.masked(), exc)
            return web.json_response({"error": "check failed"}, status=503)

        if should_refund(outcome):
            # Challenge or no gate/proxy: the check never produced a verdict.
            await refund_usage(runtime.db, uid, "check", decision["consume"])

        fresh = await runtime.db.get_user(uid) or user
        return web.json_response({
            "live": bool(outcome.live),
            "approved": bool(outcome.live),
            "verdict": outcome.verdict,
            "message": getattr(outcome, "message", "") or getattr(outcome, "note", ""),
            "latency_ms": int(outcome.latency_ms or 0),
            "credits": int(fresh["credits"] or 0),
            "card": card.masked(),
            "quota": quota_snapshot(fresh, runtime.config),
        })

    async def health(request: web.Request) -> web.Response:
        """Health check with canary status."""
        try:
            canary_24h = runtime.canary.stats_24h()
            latency_p50 = runtime.canary.latency_p50()
            
            return web.json_response({
                "status": "healthy",
                "version": DISPLAY_VERSION,
                "commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:7],
                "time": utcnow_iso(),
                "canary_24h": {
                    "total": canary_24h.total if canary_24h else 0,
                    "successful": canary_24h.successful if canary_24h else 0
                },
                "latency_p50": latency_p50
            })
        except Exception as exc:
            log.warning("event=miniapp_health_failed error=%s", exc)
            return web.json_response({
                "status": "degraded",
                "version": DISPLAY_VERSION,
                "time": utcnow_iso()
            })

    app.router.add_get("/", index)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_get("/api/bin", api_bin)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/status", api_status)
    app.router.add_get("/api/reflink", api_reflink)
    app.router.add_post("/api/check", api_check)
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
