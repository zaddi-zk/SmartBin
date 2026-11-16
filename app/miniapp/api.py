"""Mini-app HTTP API (sections 18/19).

The same runtime the bot uses: the same executor, gates, ledger, BIN resolver
and proxy pool. Nothing here touches the legacy Supabase schema. Auth is a
verified Telegram ``initData`` payload, so a browser cannot impersonate a user
by editing a query string.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from collections.abc import Sequence
from typing import Any

import structlog
from aiohttp import web
from sqlalchemy import func, select

from app.bin.resolver import BinUnavailable
from app.config import psycopg_url
from app.core import ledger
from app.core.parse import Card, CardError
from app.core.verdicts import Verdict
from app.db import repo
from app.db.migrate_legacy import migrate_legacy_users
from app.db.models import Gate, GateStateRow, User
from app.db.session import session_scope
from app.miniapp.auth import verify_init_data
from app.routing.router import build_view
from app.runtime import runtime

log = structlog.get_logger("miniapp")

RATE_PER_MIN = 30
_WINDOW_S = 60.0
_hits: dict[int, deque[float]] = defaultdict(deque)

LIVE_VERDICTS = frozenset({Verdict.APPROVED, Verdict.LIVE, Verdict.LIVE_3DS})


# --- helpers ----------------------------------------------------------------
def _init_data(request: web.Request) -> str:
    return request.headers.get("X-Telegram-Init-Data", "") or request.query.get(
        "init_data", ""
    )


async def _user_id(request: web.Request) -> tuple[int, web.Response | None]:
    """Verify the Telegram payload and return the platform user id."""
    token = runtime().settings.tg_token.get_secret_value()
    payload = verify_init_data(_init_data(request), token)
    if payload is None:
        return 0, web.json_response({"error": "unauthorized"}, status=401)
    uid = int(payload["id"])
    username = str(payload.get("username") or "")
    async with session_scope() as session:
        user = await repo.ensure_user(session, uid, username=username)
        if user.banned:
            return 0, web.json_response(
                {"error": "Account restricted. Contact support."}, status=403
            )
        await repo.touch_user(session, uid, username)
    return uid, None


def _rate_ok(user_id: int) -> bool:
    now = time.monotonic()
    window = _hits[user_id]
    while window and now - window[0] > _WINDOW_S:
        window.popleft()
    if len(window) >= RATE_PER_MIN:
        return False
    window.append(now)
    return True


def _support_url() -> str:
    username = runtime().settings.support_username.strip().lstrip("@")
    return f"https://t.me/{username}" if username else ""


def _quota(user: User, *, is_admin: bool) -> dict[str, Any]:
    credits = int(user.credits or 0)
    return {
        "credits": credits,
        "unlimited": False,
        "admin": is_admin,
        "tier": user.tier,
        "free_bins_left": 0,
        "free_checks_left": 0,
        "bin_free": True,
    }


def _gate_list(
    session_gates: Sequence[Gate], states: dict[str, GateStateRow]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for gate in session_gates:
        view = build_view(gate, states.get(gate.id))
        out.append(
            {
                "id": view.id,
                "label": view.label,
                "lane": view.lane,
                "state": view.state,
                "enabled": view.enabled,
                "cost_cr": str(view.cost_cr),
                "p50_ms": view.p50_ms,
                "success_pct": view.success_pct,
                "handles_3ds": view.handles_3ds,
            }
        )
    return out


# --- handlers ---------------------------------------------------------------
async def health(_: web.Request) -> web.Response:
    """Public engine health for the status tab (no user data)."""
    async with session_scope() as session:
        gates = await repo.list_gates(session)
        states = await repo.gate_states(session)
        views = [build_view(gate, states.get(gate.id)) for gate in gates]
        total = sum(1 for view in views if view.enabled)
        healthy = sum(
            1 for view in views if view.enabled and view.state in ("HEALTHY", "DEGRADED")
        )
        latencies = [view.p50_ms for view in views if view.p50_ms]
    return web.json_response(
        {
            "status": "healthy" if healthy else "degraded",
            "gates": {"total": total, "healthy": healthy},
            "latency_p50": min(latencies) if latencies else None,
            "canary_24h": {"successful": healthy, "total": total or 1},
        }
    )


async def status(request: web.Request) -> web.Response:
    uid, error = await _user_id(request)
    if error is not None:
        return error
    async with session_scope() as session:
        user = await repo.get_user(session, uid)
        if user is None:
            return web.json_response({"error": "user not found"}, status=401)
        gates = await repo.list_gates(session)
        states = await repo.gate_states(session)
        payload = {
            "user": {
                "id": uid,
                "credits": int(user.credits or 0),
                "tier": user.tier,
                "premium": user.tier != "free",
                "sticky_gate": user.sticky_gate,
            },
            "quota": _quota(user, is_admin=runtime().settings.is_admin(uid)),
            "support_url": _support_url(),
            "gates": _gate_list(gates, states),
        }
    return web.json_response(payload)


REPORTABLE = frozenset(
    {
        "bank",
        "brand",
        "country",
        "currency",
        "level",
        "phone",
        "prepaid",
        "type",
    }
)


async def gates(request: web.Request) -> web.Response:
    _, error = await _user_id(request)
    if error is not None:
        return error
    lane = request.query.get("lane", "auth").strip().lower()
    if lane not in ("auth", "charge", "kill", "hit"):
        return web.json_response({"error": "unknown lane"}, status=400)
    async with session_scope() as session:
        rows = await repo.list_gates(session)
        states = await repo.gate_states(session)
        payload = _gate_list([row for row in rows if row.lane == lane], states)
    return web.json_response({"gates": payload})


async def bin_lookup(request: web.Request) -> web.Response:
    uid, error = await _user_id(request)
    if error is not None:
        return error
    raw = request.query.get("bin", "")
    digits = "".join(char for char in raw if char.isdigit())
    if len(digits) not in (6, 8):
        return web.json_response({"error": "BIN must be 6 or 8 digits"}, status=400)
    try:
        async with session_scope() as session:
            record = await runtime().bins.resolve(session, digits)
            user = await repo.get_user(session, uid)
            quota = _quota(user, is_admin=runtime().settings.is_admin(uid)) if user else {}
    except BinUnavailable:
        return web.json_response({"error": "BIN lookup unavailable"}, status=503)
    except Exception as exc:
        log.warning("event=miniapp_bin_failed", bin=digits, error=str(exc))
        return web.json_response({"error": "BIN lookup unavailable"}, status=503)

    twofa = record.r3ds.last_state == "YES" or record.r3ds.probe_last_state == "YES"
    return web.json_response(
        {
            "bin": record.bin,
            "data": {
                "brand": record.brand,
                "bank": record.bank_short or record.bank_legal,
                "country": record.country,
                "iso2": record.iso2,
                "currency": record.currency,
                "level": record.tier,
                "phone": record.bank_phone,
                "prepaid": bool(record.prepaid),
                "twofa": twofa,
                "type": record.type,
                "confidence": record.confidence,
                "sources": record.sources_n,
            },
            "quota": quota,
        }
    )


async def check(request: web.Request) -> web.Response:
    uid, error = await _user_id(request)
    if error is not None:
        return error
    if not _rate_ok(uid):
        return web.json_response(
            {"error": "Slow down. Try again in a minute.", "code": "rate_limited"},
            status=429,
        )
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid json"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid json"}, status=400)

    try:
        card = Card.parse(str(body.get("card", "")))
    except CardError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    settings = runtime().settings
    async with session_scope() as session:
        user = await repo.get_user(session, uid)
        if user is None:
            return web.json_response({"error": "user not found"}, status=401)
        duplicate = await repo.duplicate_check(
            session, uid, card.pan_hash, settings.dup_window_s
        )
        if duplicate is not None:
            return web.json_response(
                {
                    "error": f"Already checked {duplicate.verdict} a moment ago.",
                    "code": "duplicate",
                },
                status=409,
            )

        requested = str(body.get("gate") or "").strip()
        gate_id = requested or user.sticky_gate or None
        lane = "auth"
        if gate_id:
            gate_row = await repo.get_gate(session, gate_id)
            if gate_row is None or not gate_row.enabled:
                gate_id = user.sticky_gate or None
            else:
                lane = gate_row.lane
        try:
            outcome = await runtime().executor.run(
                session, user=user, card=card, lane=lane, gate_id=gate_id
            )
        except ledger.InsufficientCredits as exc:
            return web.json_response(
                {
                    "error": f"{exc.needed} cr needed, balance {exc.balance} cr.",
                    "code": "limit_reached",
                },
                status=402,
            )
        except Exception as exc:
            log.warning("event=miniapp_check_failed", user=uid, error=str(exc))
            return web.json_response({"error": "check failed"}, status=503)

        if outcome.gate_id:
            user.sticky_gate = outcome.gate_id
        balance = int(outcome.balance or user.credits or 0)
        quota = _quota(user, is_admin=settings.is_admin(uid))

    live = outcome.verdict in LIVE_VERDICTS
    return web.json_response(
        {
            "live": live,
            "approved": live,
            "verdict": outcome.verdict.value,
            "gate": outcome.gate_id,
            "gate_label": outcome.gate_label,
            "code": outcome.code,
            "decline_code": outcome.decline_code,
            "message": outcome.reason or outcome.code or outcome.verdict.value,
            "latency_ms": int(outcome.latency_ms or 0),
            "credits": balance,
            "debited": str(outcome.debited),
            "card": outcome.pan_masked or card.masked,
            "avs": outcome.avs,
            "cvc": outcome.cvc,
            "three_ds": outcome.three_ds,
            "acs_url": outcome.acs_url,
            "quota": quota,
        }
    )


async def kill(request: web.Request) -> web.Response:
    """Section 15: the escalating manual-capture ladder, same as the bot."""
    uid, error = await _user_id(request)
    if error is not None:
        return error
    if not _rate_ok(uid):
        return web.json_response(
            {"error": "Slow down. Try again in a minute.", "code": "rate_limited"},
            status=429,
        )
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid json"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid json"}, status=400)

    try:
        card = Card.parse(str(body.get("card", "")))
    except CardError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    settings = runtime().settings
    async with session_scope() as session:
        user = await repo.get_user(session, uid)
        if user is None:
            return web.json_response({"error": "user not found"}, status=401)
        requested = str(body.get("gate") or "").strip() or None
        try:
            outcome = await runtime().kill.run(
                session, user=user, card=card, gate_id=requested
            )
        except ledger.InsufficientCredits as exc:
            return web.json_response(
                {
                    "error": f"{exc.needed} cr needed, balance {exc.balance} cr.",
                    "code": "limit_reached",
                },
                status=402,
            )
        except Exception as exc:
            log.warning("event=miniapp_kill_failed", user=uid, error=str(exc))
            return web.json_response({"error": "kill failed"}, status=503)

        if outcome.gate_id:
            user.sticky_gate = outcome.gate_id
        balance = int(outcome.balance or user.credits or 0)
        quota = _quota(user, is_admin=settings.is_admin(uid))

    live = outcome.verdict in LIVE_VERDICTS
    return web.json_response(
        {
            "live": live,
            "verdict": outcome.verdict.value,
            "gate": outcome.gate_id,
            "gate_label": outcome.gate_label,
            "reached_cents": outcome.reached_cents,
            "debited": str(outcome.debited),
            "credits": balance,
            "message": outcome.reason or outcome.verdict.value,
            "card": outcome.pan_masked or card.masked,
            "rungs": [
                {
                    "amount_cents": rung.amount_cents,
                    "verdict": rung.verdict.value,
                    "code": rung.code,
                    "latency_ms": rung.latency_ms,
                }
                for rung in outcome.rungs
            ],
            "quota": quota,
        }
    )


async def report(request: web.Request) -> web.Response:
    """BIN Doctor: store a field correction for review, same as the bot."""
    uid, error = await _user_id(request)
    if error is not None:
        return error
    if not _rate_ok(uid):
        return web.json_response(
            {"error": "Slow down. Try again in a minute.", "code": "rate_limited"},
            status=429,
        )
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid json"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid json"}, status=400)

    digits = "".join(char for char in str(body.get("bin", "")) if char.isdigit())
    if len(digits) not in (6, 8):
        return web.json_response({"error": "BIN must be 6 or 8 digits"}, status=400)
    field = str(body.get("field", "")).strip().lower()
    if field not in REPORTABLE:
        return web.json_response({"error": "unknown field"}, status=400)
    value = str(body.get("value", "")).strip()
    if not value:
        return web.json_response({"error": "value is required"}, status=400)

    async with session_scope() as session:
        await runtime().bins.report_field(
            session, digits, field=field, claimed=value[:500], user_id=uid
        )
    return web.json_response({"stored": True})


async def reflink(request: web.Request) -> web.Response:
    uid, error = await _user_id(request)
    if error is not None:
        return error
    username = runtime().settings.bot_username.strip().lstrip("@")
    link = f"https://t.me/{username}?start=ref_{uid}" if username else ""
    return web.json_response({"ref_link": link})


async def admin_migrate(request: web.Request) -> web.Response:
    """Run the legacy import in-process. Admins only.

    The source normally comes from ``LEGACY_DATABASE_URL``. A one-time POST
    body may carry it instead so an operator can bootstrap a deployment that
    has no env var yet; the value is used and never echoed or logged.
    """
    uid, error = await _user_id(request)
    if error is not None:
        return error
    settings = runtime().settings
    if not settings.is_admin(uid):
        return web.json_response({"error": "admins only"}, status=403)

    source = ""
    if request.can_read_body:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        if isinstance(body, dict):
            source = str(body.get("source") or "").strip()
    source = source or settings.legacy_source_url
    if not source:
        return web.json_response(
            {"error": "no legacy source configured"}, status=400
        )

    async with session_scope() as session:
        report = await migrate_legacy_users(session, psycopg_url(source))
        total = await session.scalar(select(func.count()).select_from(User)) or 0
        monthly = await repo.monthly_active(session)

    return web.json_response(
        {
            "read": report.read,
            "created": report.created,
            "updated": report.updated,
            "error": report.error,
            "total": int(total),
            "monthly": monthly,
        },
        status=200 if report.ok else 502,
    )


def register(app: web.Application) -> None:
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/status", status)
    app.router.add_get("/api/gates", gates)
    app.router.add_get("/api/bin", bin_lookup)
    app.router.add_post("/api/check", check)
    app.router.add_post("/api/kill", kill)
    app.router.add_post("/api/report", report)
    app.router.add_get("/api/reflink", reflink)
    app.router.add_post("/api/admin/migrate", admin_migrate)
