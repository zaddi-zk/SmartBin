"""Admin screens and commands (section 20)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core import ledger
from app.db import repo
from app.db.models import AdminLog, Check, Gate, LedgerEntry, User
from app.routing.router import build_view
from app.runtime import runtime
from app.tg import keyboards as kb
from app.tg import render
from app.tg.guards import refuse

router = Router(name="admin")


def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


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
    await message.answer("⚙️ <b>ADMIN</b>", reply_markup=kb.admin_kb())


@router.callback_query(F.data == "adm")
async def adm(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id, settings):
        await refuse(callback, "⚙️ Admins only.")
        return
    if callback.message is not None:
        await callback.message.edit_text("⚙️ <b>ADMIN</b>", reply_markup=kb.admin_kb())


@router.callback_query(F.data == "adm:gates")
async def adm_gates(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id, settings):
        return
    gates = await repo.list_gates(session)
    states = await repo.gate_states(session)
    views = [build_view(gate, states.get(gate.id)) for gate in gates]
    if callback.message is not None:
        await callback.message.edit_text("🏷️ <b>GATES</b>", reply_markup=kb.admin_gates(views))


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
    view = build_view(gate, state_row)
    await callback.message.edit_text(
        f"🏷️ <b>{render.esc(gate.label)}</b>\n"
        f"processor <code>{render.esc(gate.processor)}</code> · lane {render.esc(gate.lane)}\n"
        f"enabled <b>{gate.enabled}</b> · state <b>{render.esc(state_row.state)}</b>",
        reply_markup=kb.admin_gate(view),
    )


@router.callback_query(F.data == "adm:stats")
async def adm_stats(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if (
        callback.from_user is None
        or not is_admin(callback.from_user.id, settings)
        or callback.message is None
    ):
        return
    users = await session.scalar(select(func.count()).select_from(User)) or 0
    checks = await session.scalar(select(func.count()).select_from(Check)) or 0
    revenue = await session.scalar(select(func.coalesce(func.sum(LedgerEntry.delta), 0))) or 0
    gates = await session.scalar(select(func.count()).select_from(Gate)) or 0
    await callback.message.edit_text(
        f"📈 <b>PLATFORM STATS</b>\n\n"
        f"👥 users <b>{users}</b> · 🏷️ gates <b>{gates}</b>\n"
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
