"""Public status screen (section 19). Renders without a user record."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.routing.router import GateView, build_view
from app.tg import keyboards as kb
from app.tg import render

router = Router(name="status")


async def views(session: AsyncSession) -> list[GateView]:
    gates = await repo.list_gates(session)
    states = await repo.gate_states(session)
    return [build_view(gate, states.get(gate.id)) for gate in gates]


@router.message(Command("status"))
async def status_command(message: Message, session: AsyncSession) -> None:
    await message.answer(render.status_text(await views(session)), reply_markup=kb.status_kb())


@router.callback_query(F.data.startswith("status:"))
async def status_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        return
    all_views = await views(session)
    if callback.data == "status:up":
        subset = [view for view in all_views if view.state == "HEALTHY"]
        body = "\n".join(
            f"🟢 {render.esc(view.label)} · {render.latency(view.p50_ms)}" for view in subset
        )
        text = f"🟢 <b>UP</b>\n\n{body or 'None.'}"
    elif callback.data == "status:deg":
        subset = [view for view in all_views if view.state in ("DEGRADED", "HALF_OPEN")]
        body = "\n".join(
            f"🟡 {render.esc(view.label)} · {render.latency(view.p50_ms)}" for view in subset
        )
        text = f"🟡 <b>DEGRADED</b>\n\n{body or 'None.'}"
    elif callback.data == "status:open":
        subset = [view for view in all_views if view.state == "OPEN"]
        body = "\n".join(f"🔴 {render.esc(view.label)}" for view in subset)
        text = f"🔴 <b>OPEN</b>\n\n{body or 'None.'}"
    else:
        text = render.status_text(all_views)
    await callback.message.edit_text(text, reply_markup=kb.status_kb())
