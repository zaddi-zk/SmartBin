"""Gate hub lists, detail and stats."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.routing.router import GateView, build_view, route
from app.tg import keyboards as kb
from app.tg import render
from app.tg.guards import load_user
from app.tg.states import Flow

router = Router(name="gates")

LANES = {"auth", "charge", "hit", "kill"}


async def lane_views(session: AsyncSession, lane: str) -> list[GateView]:
    gates = await repo.list_gates(session, lane)
    states = await repo.gate_states(session)
    return [build_view(gate, states.get(gate.id)) for gate in gates]


@router.callback_query(F.data.startswith("hub:"))
async def lane(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    if callback.data is None or callback.message is None:
        return
    if await load_user(session, callback, bot=bot, settings=settings) is None:
        return
    lane_name = callback.data.split(":", 1)[1]
    if lane_name not in LANES:
        await callback.message.edit_text(render.hub_text(), reply_markup=kb.gate_hub())
        return
    views = await lane_views(session, lane_name)
    await callback.message.edit_text(
        render.gates_text(lane_name, views),
        reply_markup=kb.lane_gates(lane_name, views),
    )


@router.callback_query(F.data.startswith("gate:info:"))
async def detail(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    if callback.data is None or callback.message is None:
        return
    if await load_user(session, callback, bot=bot, settings=settings) is None:
        return
    gate_id = callback.data.split(":", 2)[2]
    gate = await repo.get_gate(session, gate_id)
    if gate is None:
        await callback.message.edit_text(render.hub_text(), reply_markup=kb.gate_hub())
        return
    state = await repo.ensure_gate_state(session, gate.id)
    views = await lane_views(session, gate.lane)
    view = next((item for item in views if item.id == gate.id), None)
    if view is None:
        await callback.message.edit_text(render.hub_text(), reply_markup=kb.gate_hub())
        return
    keys = await repo.keys_for_gate(session, gate.id)
    await callback.message.edit_text(
        render.detail_text(view, len(keys), state.state, state.last_ok_at),
        reply_markup=kb.gate_detail(view),
    )


@router.callback_query(F.data.startswith("gate:act:"))
async def action(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    if callback.data is None or callback.message is None:
        return
    if await load_user(session, callback, bot=bot, settings=settings) is None:
        return
    parts = callback.data.split(":")
    if len(parts) < 4:
        return
    gate_id, action_name = parts[2], parts[3]
    gate = await repo.get_gate(session, gate_id)
    if gate is None:
        return

    if action_name == "stats":
        one_hour = await repo.load_health(session, gate.id, hours=1)
        day = await repo.load_health(session, gate.id, hours=24)
        body = (
            f"📈 <b>{render.esc(gate.label)} · STATS</b>\n\n"
            f"1h · p50 <b>{render.latency(one_hour.p50())}</b> · "
            f"p95 <b>{render.latency(one_hour.p95())}</b> · "
            f"✅ <b>{one_hour.success_pct() or 0:.1f}%</b> ({one_hour.total})\n"
            f"24h · p50 <b>{render.latency(day.p50())}</b> · "
            f"p95 <b>{render.latency(day.p95())}</b> · "
            f"✅ <b>{day.success_pct() or 0:.1f}%</b> ({day.total})"
        )
        await callback.message.edit_text(
            body,
            reply_markup=kb.gate_detail(
                build_view(gate, await repo.ensure_gate_state(session, gate.id))
            ),
        )
        return

    if action_name == "mass":
        await state.set_state(Flow.await_file)
        await state.update_data(gate_id=gate.id, lane=gate.lane)
        await callback.message.edit_text(
            f"📁 <b>MASS · {render.esc(gate.label)}</b>\n\n"
            "Paste cards, one per line. Send /cancel to stop.\n"
            "<blockquote>Cost preview is shown after parsing.</blockquote>",
            reply_markup=kb.back_kb("hub:mass", "⬅️ Back"),
        )


@router.callback_query(F.data.startswith("gate:near:"))
async def nearest(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    if callback.data is None or callback.message is None:
        return
    if await load_user(session, callback, bot=bot, settings=settings) is None:
        return
    gate_id = callback.data.split(":", 2)[2]
    gate = await repo.get_gate(session, gate_id)
    if gate is None:
        return
    views = await lane_views(session, gate.lane)
    candidates = route(views, "", gate.lane)
    if not candidates:
        await callback.message.edit_text(
            "🔴 <b>ALL GATES OPEN</b>\n<blockquote>0 cr charged. Try another lane.</blockquote>",
            reply_markup=kb.gate_hub(),
        )
        return
    target = candidates[0]
    state = await repo.ensure_gate_state(session, target.id)
    keys = await repo.keys_for_gate(session, target.id)
    await callback.message.edit_text(
        render.detail_text(target, len(keys), state.state, state.last_ok_at),
        reply_markup=kb.gate_detail(target),
    )
