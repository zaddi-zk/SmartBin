"""Admin screens and commands (section 20)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.types import User as TgUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core import ledger
from app.db import repo
from app.db.migrate_legacy import migrate_legacy_users
from app.db.models import AdminLog, Check, Gate, LedgerEntry, User
from app.routing.router import build_view
from app.runtime import runtime
from app.tg import keyboards as kb
from app.tg import render
from app.tg.guards import refuse
from app.tg.states import Flow

router = Router(name="admin")

MAX_DUMP_BYTES = 10 * 1024 * 1024


def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


def _at(username: str | None) -> str:
    """A Telegram handle, or a neutral dash when the user hides it."""
    return f"@{render.esc(username)}" if username else "—"


def _identity(tg_user: TgUser | None) -> str:
    """Admin identity line: username (or name) plus the numeric id."""
    if tg_user is None:
        return "—"
    name = f"@{render.esc(tg_user.username)}" if tg_user.username else render.esc(
        tg_user.full_name or "no username"
    )
    return f"{name} · <code>{tg_user.id}</code>"


async def log_action(
    session: AsyncSession,
    actor: int,
    action: str,
    target: str,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> None:
    session.add(
        AdminLog(
            actor=actor,
            action=action,
            target=target,
            before=before or {},
            after=after or {},
        )
    )


@router.message(Command("adm"))
async def adm_command(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        await refuse(message, "⚙️ Admins only.")
        return
    await message.answer(
        f"⚙️ <b>ADMIN</b>\n<blockquote>{_identity(message.from_user)}</blockquote>",
        reply_markup=kb.admin_kb(),
    )


@router.callback_query(F.data == "adm")
async def adm(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id, settings):
        await refuse(callback, "⚙️ Admins only.")
        return
    if callback.message is not None:
        await callback.message.edit_text(
            f"⚙️ <b>ADMIN</b>\n<blockquote>{_identity(callback.from_user)}</blockquote>",
            reply_markup=kb.admin_kb(),
        )


@router.callback_query(F.data == "adm:gates")
async def adm_gates(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id, settings):
        return
    gates = await repo.list_gates(session)
    states = await repo.gate_states(session)
    views = [
        build_view(gate, states.get(gate.id), installed=runtime().adapters.names)
        for gate in gates
    ]
    ready = sum(1 for view in views if view.ready)
    if callback.message is not None:
        await callback.message.edit_text(
            f"🏷️ <b>GATES</b> · ready <b>{ready}</b>/{len(views)}\n"
            "<blockquote>⚪ = enabled, but no key or adapter on this instance.</blockquote>",
            reply_markup=kb.admin_gates(views),
        )


@router.callback_query(F.data.startswith("adm:g:"))
async def adm_gate(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if (
        callback.data is None
        or callback.from_user is None
        or not is_admin(callback.from_user.id, settings)
    ):
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        return
    gate_id = parts[2]
    gate = await repo.get_gate(session, gate_id)
    if gate is None or callback.message is None:
        return

    if len(parts) >= 4:
        action = parts[3]
        before = {
            "enabled": gate.enabled,
            "state": (await repo.ensure_gate_state(session, gate.id)).state,
        }
        if action == "toggle":
            gate.enabled = not gate.enabled
            await log_action(
                session,
                callback.from_user.id,
                "gate.toggle",
                gate.id,
                before,
                {"enabled": gate.enabled},
            )
        elif action == "probe":
            state = await repo.ensure_gate_state(session, gate.id)
            state.state = "HALF_OPEN"
            state.next_probe_at = datetime.now(UTC)
            # An admin probe is also the way to clear a permanent blocker: the
            # account may have been fixed, and the next attempt re-observes it.
            state.last_err_code = None
            runtime().executor.breaker(gate.id).force_probe()
            await log_action(
                session,
                callback.from_user.id,
                "gate.probe",
                gate.id,
                before,
                {"state": "HALF_OPEN"},
            )

    state_row = await repo.ensure_gate_state(session, gate.id)
    view = build_view(gate, state_row, installed=runtime().adapters.names)
    keys = await repo.keys_for_gate(session, gate.id)
    readiness = "🟢 ready" if view.ready else "⚪ no live key (adapter not registered)"
    await callback.message.edit_text(
        f"🏷️ <b>{render.esc(gate.label)}</b>\n"
        f"processor <code>{render.esc(gate.processor)}</code> · lane {render.esc(gate.lane)}\n"
        f"enabled <b>{gate.enabled}</b> · state <b>{render.esc(state_row.state)}</b>\n"
        f"🔑 keys <b>{len(keys)}</b> · {readiness}",
        reply_markup=kb.admin_gate(view),
    )


@router.callback_query(F.data == "adm:px")
async def adm_proxy(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if (
        callback.from_user is None
        or not is_admin(callback.from_user.id, settings)
        or callback.message is None
    ):
        return
    stats = await runtime().proxies.stats(session)
    fastest = f"{stats.fastest_ms}ms" if stats.fastest_ms is not None else "—"
    await callback.message.edit_text(
        "🔌 <b>PROXY POOL</b>\n\n"
        f"📦 rows <b>{stats.total}</b> · 🟢 alive <b>{stats.alive}</b> · "
        f"🧪 tested <b>{stats.tested}</b>\n"
        f"⚡ fastest <b>{fastest}</b> · 👥 users without proxy <b>{stats.users_without}</b>\n\n"
        "<blockquote>Only recently checked, alive rows are ever handed to a check. "
        "Import a dump, then refresh.</blockquote>",
        reply_markup=kb.admin_proxy(),
    )


@router.callback_query(F.data == "adm:px:refresh")
async def adm_proxy_refresh(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id, settings):
        return
    alive, tested = await runtime().proxies.refresh(session, limit=200)
    await log_action(
        session,
        callback.from_user.id,
        "proxy.refresh",
        "pool",
        {},
        {"tested": tested, "alive": alive},
    )
    if callback.message is not None:
        await callback.message.edit_text(
            "🔄 <b>REFRESH</b>\n\n"
            f"🧪 tested <b>{tested}</b> · 🟢 alive <b>{alive}</b>",
            reply_markup=kb.admin_proxy(),
        )


@router.callback_query(F.data == "adm:px:purge")
async def adm_proxy_purge(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id, settings):
        return
    purged = await runtime().proxies.purge_dead(session)
    await log_action(session, callback.from_user.id, "proxy.purge", "pool", {}, {"purged": purged})
    if callback.message is not None:
        await callback.message.edit_text(
            f"🧹 <b>PURGE</b>\n\nDeleted <b>{purged}</b> dead rows. "
            "Users and keys pointing at them were detached.",
            reply_markup=kb.admin_proxy(),
        )


@router.callback_query(F.data == "adm:px:auto")
async def adm_proxy_auto(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id, settings):
        return
    assigned = await runtime().proxies.auto_assign(session)
    await log_action(
        session, callback.from_user.id, "proxy.auto", "users", {}, {"assigned": assigned}
    )
    if callback.message is not None:
        await callback.message.edit_text(
            "🎁 <b>AUTO-ASSIGN</b>\n\n"
            f"Assigned a fast proxy to <b>{assigned}</b> user(s) without live egress.\n"
            "<blockquote>Runs automatically on the proxy tick as well.</blockquote>",
            reply_markup=kb.admin_proxy(),
        )


@router.callback_query(F.data == "adm:px:imp")
async def adm_proxy_import(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id, settings):
        return
    await state.set_state(Flow.await_proxy_bulk)
    if callback.message is not None:
        await callback.message.edit_text(
            "📥 <b>IMPORT PROXIES</b>\n\n"
            "Paste lines or send a <code>.txt</code> file. Accepted:\n"
            "<code>host:port</code> · <code>host:port:user:pass</code> · "
            "<code>scheme://user:pass@host:port</code>\n\n"
            "<blockquote>Duplicates are skipped. The first 40 new rows are "
            "tested immediately; the rest warm up on the tick.</blockquote>",
            reply_markup=kb.back_kb("adm:px", "⬅️ Back"),
        )


async def _import_dump(
    message: Message, session: AsyncSession, state: FSMContext, text: str
) -> None:
    await state.clear()
    pool = runtime().proxies
    added = await pool.import_many(session, text)
    if added == 0:
        await message.answer(
            "⚠️ No new proxies found. Every line was a duplicate or not a proxy.",
            reply_markup=kb.admin_proxy(),
        )
        return
    alive, tested = await pool.refresh(session, limit=40)
    await message.answer(
        "📥 <b>IMPORTED</b>\n\n"
        f"➕ added <b>{added}</b> · 🧪 tested <b>{tested}</b> · 🟢 alive <b>{alive}</b>",
        reply_markup=kb.admin_proxy(),
    )


@router.message(Flow.await_proxy_bulk, F.text)
async def adm_proxy_import_text(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        await refuse(message, "⚙️ Admins only.")
        return
    await _import_dump(message, session, state, message.text or "")


@router.message(Flow.await_proxy_bulk, F.document)
async def adm_proxy_import_file(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        await refuse(message, "⚙️ Admins only.")
        return
    document = message.document
    if document is None:
        return
    if (document.file_size or 0) > MAX_DUMP_BYTES:
        await message.answer("⚠️ File too large. Split it under 10 MB.")
        return
    buffer = await bot.download(document)
    if buffer is None:
        await message.answer("⚠️ Could not download that file.")
        return
    text = buffer.read().decode("utf-8", errors="ignore")
    await _import_dump(message, session, state, text)


@router.callback_query(F.data == "adm:u")
async def adm_users(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if (
        callback.from_user is None
        or not is_admin(callback.from_user.id, settings)
        or callback.message is None
    ):
        return
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    monthly = await repo.monthly_active(session)
    banned = (
        await session.scalar(
            select(func.count()).select_from(User).where(User.banned.is_(True))
        )
        or 0
    )
    rows = list(
        (
            await session.scalars(
                select(User).order_by(User.last_seen_at.desc().nulls_last()).limit(12)
            )
        ).all()
    )
    body = "\n".join(
        f"{_at(row.username)} · <code>{row.id}</code> · "
        f"{render.esc(Decimal(str(row.credits)))} cr · "
        f"{'⛔' if row.banned else '🟢'} {render.esc(row.tier)}"
        for row in rows
    )
    await callback.message.edit_text(
        "👥 <b>USERS</b>\n\n"
        f"total <b>{total}</b> · 🧠 monthly <b>{monthly}</b> · "
        f"⛔ banned <b>{banned}</b>\n\n{body or 'No users yet.'}\n\n"
        "<blockquote>Monthly = last seen in the last 30 days. "
        "/grant &lt;id&gt; &lt;cr&gt; · /ban &lt;id&gt; · "
        "/key &lt;gate&gt; &lt;secret&gt; [country]</blockquote>",
        reply_markup=kb.admin_users(),
    )


@router.callback_query(F.data == "adm:u:migrate")
async def adm_users_migrate(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    if (
        callback.from_user is None
        or not is_admin(callback.from_user.id, settings)
        or callback.message is None
    ):
        return
    url = settings.legacy_source_url
    if not url:
        await callback.message.edit_text(
            "⚠️ <b>NO LEGACY SOURCE</b>\n\n"
            "Set <code>LEGACY_DATABASE_URL</code> (or <code>DATABASE_URL</code>) "
            "to the legacy Supabase Postgres URL, then press import again.",
            reply_markup=kb.admin_users(),
        )
        return

    report = await migrate_legacy_users(session, url)
    await log_action(
        session,
        callback.from_user.id,
        "users.migrate",
        "legacy",
        {},
        {
            "read": report.read,
            "created": report.created,
            "updated": report.updated,
            "error": report.error[:200],
        },
    )
    if report.ok:
        text = (
            "📥 <b>LEGACY IMPORT</b>\n\n"
            f"read <b>{report.read}</b> · created <b>{report.created}</b> · "
            f"updated <b>{report.updated}</b>\n\n"
            "<blockquote>Rerun any time: credits merge upward, bans never lift, "
            "and the newest activity stamp wins.</blockquote>"
        )
    else:
        text = (
            "⚠️ <b>IMPORT FAILED</b>\n\n"
            f"<code>{render.esc(report.error[:300])}</code>"
        )
    await callback.message.edit_text(text, reply_markup=kb.admin_users())


@router.callback_query(F.data == "adm:stats")
async def adm_stats(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if (
        callback.from_user is None
        or not is_admin(callback.from_user.id, settings)
        or callback.message is None
    ):
        return
    users = await session.scalar(select(func.count()).select_from(User)) or 0
    active = await repo.monthly_active(session)
    checks = await session.scalar(select(func.count()).select_from(Check)) or 0
    revenue = await session.scalar(select(func.coalesce(func.sum(LedgerEntry.delta), 0))) or 0
    gates = await session.scalar(select(func.count()).select_from(Gate)) or 0
    await callback.message.edit_text(
        f"📈 <b>PLATFORM STATS</b>\n\n"
        f"👥 users <b>{users}</b> · 🧠 active 30d <b>{active}</b> · 🏷️ gates <b>{gates}</b>\n"
        f"🧾 checks <b>{checks}</b> · 💠 ledger <b>{render.esc(Decimal(str(revenue)))}</b>",
        reply_markup=kb.admin_kb(),
    )


@router.callback_query(F.data == "adm:log")
async def adm_log(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if (
        callback.from_user is None
        or not is_admin(callback.from_user.id, settings)
        or callback.message is None
    ):
        return
    rows = (await session.scalars(select(AdminLog).order_by(AdminLog.at.desc()).limit(10))).all()
    body = "\n".join(
        f"<code>{row.at:%H:%M}</code> {render.esc(row.action)} · {render.esc(row.target)}"
        for row in rows
    )
    await callback.message.edit_text(
        f"🧾 <b>ADMIN LOG</b>\n\n{body or 'Empty.'}", reply_markup=kb.admin_kb()
    )


@router.message(Command("grant"))
async def grant(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        await refuse(message, "⚙️ Admins only.")
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].lstrip("-").isdigit():
        await message.answer("Usage: /grant <user_id> <credits>")
        return
    target = int(parts[1])
    amount = Decimal(parts[2])
    user = await repo.ensure_user(session, target)
    before = Decimal(user.credits)
    balance = await ledger.debit(
        session,
        target,
        amount,
        "admin.grant",
        f"grant:{target}:{datetime.now(UTC).timestamp()}",
    )
    await log_action(
        session,
        message.from_user.id,
        "user.grant",
        str(target),
        {"credits": str(before)},
        {"credits": str(balance)},
    )
    await message.answer(f"💠 {target} → {render.esc(balance)} cr (was {render.esc(before)})")


@router.message(Command("ban"))
async def ban(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        await refuse(message, "⚙️ Admins only.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /ban <user_id>")
        return
    target = int(parts[1])
    user = await repo.ensure_user(session, target)
    user.banned = True
    await log_action(
        session, message.from_user.id, "user.ban", str(target), {"banned": False}, {"banned": True}
    )
    await message.answer(f"⛔ {target} banned.")


@router.message(Command("key"))
async def add_key(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        await refuse(message, "⚙️ Admins only.")
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("Usage: /key <gate_id> <secret> [country]")
        return
    gate_id, secret = parts[1], parts[2]
    country = parts[3].upper() if len(parts) > 3 else "US"
    gate = await repo.get_gate(session, gate_id)
    if gate is None:
        await message.answer("Unknown gate.")
        return
    row = await repo.add_key(session, gate_id, secret, country)
    adapter = runtime().adapters.get(gate.processor)
    if adapter is None:
        await message.answer("No adapter for that gate. Key stored as PENDING.")
        return
    async with httpx.AsyncClient(timeout=15) as client:
        ok = await adapter.validate_key(client, runtime().keys.to_key(row))
    if ok:
        row.state = "READY"
        row.livemode = True
        row.validated_at = datetime.now(UTC)
        await message.answer(f"🟢 Key {row.id} READY (livemode asserted).")
    else:
        row.state = "BENCHED"
        await message.answer(f"🔴 Key {row.id} failed validation (livemode or auth). Not used.")
    await log_action(session, message.from_user.id, "key.add", gate_id, {}, {"state": row.state})
