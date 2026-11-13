"""BIN lookup screen."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.routing.router import build_view
from app.runtime import runtime
from app.tg import keyboards as kb
from app.tg import render
from app.tg.guards import load_user
from app.tg.states import Flow

router = Router(name="bin")


async def show_bin(message: Message, session: AsyncSession, raw: str) -> None:
    if not raw.isdigit() or not 6 <= len(raw) <= 8:
        await message.answer("🔍 Send a 6–8 digit BIN.")
        return
    try:
        info = await runtime().bins.resolve(session, raw)
    except Exception:
        await message.answer("⚠️ BIN lookup failed. Try again.")
        return
    views = await repo.list_gates(session, "auth")
    states = await repo.gate_states(session)
    views2 = [build_view(gate, states.get(gate.id)) for gate in views]
    best = [view.label for view in views2 if view.enabled and view.state != "OPEN"][:2]
    best_id = next((view.id for view in views2 if view.enabled and view.state != "OPEN"), None)
    await message.answer(render.bin_text(info, best), reply_markup=kb.bin_result(best_id))


@router.message(Command("bin"))
async def bin_command(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.await_bin)
    await message.answer(
        "🔍 <b>BIN LOOKUP</b>\nSend a 6–8 digit BIN.",
        reply_markup=kb.back_kb("hub:main", "⬅️ Menu"),
    )


@router.callback_query(F.data == "bin:new")
async def bin_new(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(Flow.await_bin)
    await callback.message.edit_text(
        "🔍 <b>BIN LOOKUP</b>\nSend a 6–8 digit BIN.",
        reply_markup=kb.back_kb("hub:main", "⬅️ Menu"),
    )


@router.message(Flow.await_bin, F.text)
async def bin_text(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if message.text is None:
        return
    await state.clear()
    await show_bin(message, session, message.text.strip())


@router.callback_query(F.data.startswith("bin:run:"))
async def bin_run(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    if callback.data is None or callback.message is None:
        return
    user = await load_user(session, callback, bot=bot, settings=settings)
    if user is None:
        return
    gate_id = callback.data.split(":", 2)[2]
    gate = await repo.get_gate(session, gate_id)
    if gate is None:
        return
    await repo.set_sticky_gate(session, user.id, gate.id)
    await state.set_state(Flow.await_card)
    await state.update_data(gate_id=gate.id, lane=gate.lane)
    state_row = await repo.ensure_gate_state(session, gate.id)
    view = build_view(gate, state_row)
    await callback.message.edit_text(render.armed_text(view), reply_markup=kb.armed(view))


@router.callback_query(F.data.startswith("bin:src:"))
async def bin_sources(callback: CallbackQuery) -> None:
    if callback.message is not None:
        await callback.message.answer(
            "📚 <b>SOURCES</b>\n<blockquote>binlist + antipublic, merged field by field. "
            "Disagreement becomes UNKNOWN and costs 15 confidence points; "
            "a single source costs 20.</blockquote>"
        )
