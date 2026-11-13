"""BIN lookup screens (section 17).

A lookup ends in a priced gate and a button. The record is the product: every
screen reads the same stored fields and the same provenance rows.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bin.measure import MeasureResult, measure_3ds
from app.bin.model import BinRecord
from app.bin.resolver import BinUnavailable
from app.config import Settings
from app.db import repo
from app.routing.router import GateView, build_view, route
from app.runtime import runtime
from app.tg import keyboards as kb
from app.tg import render
from app.tg.guards import load_user
from app.tg.states import Flow

router = Router(name="bin")

REPORTABLE = [
    "brand",
    "type",
    "tier",
    "prepaid",
    "commercial",
    "debit",
    "bank_short",
    "bank_phone",
    "bank_site",
    "country",
    "iso2",
    "currency",
]


async def _views(session: AsyncSession) -> list[GateView]:
    return await runtime().executor.views(session)


async def _load(session: AsyncSession, raw: str) -> BinRecord:
    record = await runtime().bins.load(session, raw)
    if record is None:
        record = await runtime().bins.resolve(session, raw)
    return record


def _valid(raw: str) -> bool:
    return raw.isdigit() and 6 <= len(raw) <= 8


async def show_bin(message: Message, session: AsyncSession, raw: str) -> None:
    if not _valid(raw):
        await message.answer("🔍 Send a 6–8 digit BIN.")
        return
    try:
        record = await runtime().bins.resolve(session, raw)
    except (BinUnavailable, ValueError):
        await message.answer("⚠️ BIN lookup failed. Try again.")
        return
    gates = await _views(session)
    await message.answer(
        render.render_bin(record, gates), reply_markup=kb.bin_result(record, gates)
    )


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


@router.callback_query(F.data.startswith("bin:show:"))
async def bin_show(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.data is None or callback.message is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await _load(session, raw)
    except (BinUnavailable, ValueError):
        await callback.message.edit_text("⚠️ BIN record is unavailable.")
        return
    gates = await _views(session)
    await callback.message.edit_text(
        render.render_bin(record, gates), reply_markup=kb.bin_result(record, gates)
    )


@router.callback_query(F.data.startswith("bin:ref:"))
async def bin_refresh(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.data is None or callback.message is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await runtime().bins.resolve(session, raw, force=True)
    except (BinUnavailable, ValueError):
        await callback.message.answer("⚠️ Refresh failed; keeping the cached record.")
        return
    gates = await _views(session)
    await callback.message.edit_text(
        render.render_bin(record, gates), reply_markup=kb.bin_result(record, gates)
    )


@router.callback_query(F.data.startswith("bin:run:"))
async def bin_run(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    if callback.data is None or callback.message is None:
        return
    user = await load_user(session, callback, bot=bot, settings=settings)
    if user is None:
        return
    parts = callback.data.split(":")
    if len(parts) < 4:
        return
    gate_id = parts[3]
    gate = await repo.get_gate(session, gate_id)
    if gate is None:
        await callback.message.answer("🚧 That gate no longer exists.")
        return
    if not gate.enabled or runtime().adapters.get(gate.processor) is None:
        await callback.message.answer(
            f"🚧 <b>{render.esc(gate.label)}</b> is not live yet. 0 cr charged."
        )
        return
    await repo.set_sticky_gate(session, user.id, gate.id)
    await state.set_state(Flow.await_card)
    await state.update_data(gate_id=gate.id, lane=gate.lane)
    state_row = await repo.ensure_gate_state(session, gate.id)
    view = build_view(gate, state_row)
    await callback.message.edit_text(render.armed_text(view), reply_markup=kb.armed(view))


@router.callback_query(F.data.startswith("bin:3ds:"))
async def bin_3ds(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    if callback.data is None or callback.message is None:
        return
    user = await load_user(session, callback, bot=bot, settings=settings)
    if user is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await _load(session, raw)
    except (BinUnavailable, ValueError):
        await callback.message.answer("⚠️ BIN record is unavailable.")
        return

    result, detail = await measure_3ds(
        session, user, record, runtime().executor, runtime().bins, settings
    )
    if result is MeasureResult.NO_CREDITS:
        await callback.message.answer(
            f"💠 <b>1 cr needed</b>\n<blockquote>{render.esc(detail)}</blockquote>",
            reply_markup=kb.plans_kb(),
        )
        return
    await callback.message.answer(
        f"{'🔐' if result is MeasureResult.OK else '⚠️'} {render.esc(detail)}"
    )
    fresh = await runtime().bins.load(session, raw)
    if fresh is None:
        return
    gates = await _views(session)
    await callback.message.edit_text(
        render.render_bin(fresh, gates), reply_markup=kb.bin_result(fresh, gates)
    )


@router.callback_query(F.data.startswith("bin:gates:"))
async def bin_gates(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.data is None or callback.message is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await _load(session, raw)
    except (BinUnavailable, ValueError):
        return
    gates = await _views(session)
    await callback.message.edit_text(
        render.render_bin_gates(record, gates), reply_markup=kb.bin_back(record.bin)
    )


@router.callback_query(F.data.startswith("bin:why:"))
async def bin_why(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.data is None or callback.message is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await _load(session, raw)
    except (BinUnavailable, ValueError):
        return
    await callback.message.edit_text(
        render.render_evidence(record), reply_markup=kb.bin_evidence(record.bin)
    )


@router.callback_query(F.data.startswith("bin:src:"))
async def bin_sources(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.data is None or callback.message is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await _load(session, raw)
    except (BinUnavailable, ValueError):
        return
    await callback.message.edit_text(
        render.render_provenance(record), reply_markup=kb.bin_report(record.bin)
    )


@router.callback_query(F.data.startswith("bin:doc:"))
async def bin_doctor(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.data is None or callback.message is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await _load(session, raw)
    except (BinUnavailable, ValueError):
        return
    gates = await _views(session)
    await callback.message.edit_text(
        render.render_doctor(record, gates), reply_markup=kb.bin_back(record.bin)
    )


@router.callback_query(F.data.startswith("bin:share:"))
async def bin_share(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.data is None or callback.message is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await _load(session, raw)
    except (BinUnavailable, ValueError):
        return
    gates = await _views(session)
    await callback.message.answer(
        render.render_bin_share(record, gates), reply_markup=kb.bin_back(record.bin)
    )


@router.callback_query(F.data.startswith("bin:copy:"))
async def bin_copy(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.data is None or callback.message is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await _load(session, raw)
    except (BinUnavailable, ValueError):
        return
    gates = await _views(session)
    await callback.message.answer(render.render_bin_copy(record, gates))


@router.callback_query(F.data.startswith("bin:report:"))
async def bin_report(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.data is None or callback.message is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await _load(session, raw)
    except (BinUnavailable, ValueError):
        return
    present = sorted({source.field for source in record.sources})
    fields = [field for field in REPORTABLE if field in present] or REPORTABLE
    await callback.message.edit_text(
        f"🩹 <b>REPORT A FIELD</b> · {render.esc(record.bin)}\n"
        "<blockquote>Pick the field the sources got wrong. Your report is stored "
        "for review; it does not change the record immediately.</blockquote>",
        reply_markup=kb.bin_report_fields(record.bin, fields),
    )


@router.callback_query(F.data.startswith("bin:repf:"))
async def bin_report_field(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None or callback.message is None:
        return
    parts = callback.data.split(":", 3)
    if len(parts) < 4:
        return
    bin_, field = parts[2], parts[3]
    await state.set_state(Flow.await_report)
    await state.update_data(report_bin=bin_, report_field=field)
    await callback.message.edit_text(
        f"🩹 <b>{render.esc(field)}</b> · {render.esc(bin_)}\n"
        "Send the correct value as plain text.",
        reply_markup=kb.bin_back(bin_),
    )


@router.message(Flow.await_report, F.text)
async def bin_report_value(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    bin_ = str(data.get("report_bin") or "")
    field = str(data.get("report_field") or "")
    await state.clear()
    if not bin_ or not field or message.text is None or message.from_user is None:
        return
    user = await load_user(session, message)
    if user is None:
        return
    await runtime().bins.report_field(
        session, bin_, field=field, claimed=message.text.strip(), user_id=user.id
    )
    await message.answer(
        "🩹 <b>REPORT STORED</b>\n<blockquote>Thanks. The next resolve keeps the "
        "source values; reports are reviewed separately.</blockquote>",
        reply_markup=kb.bin_back(bin_),
    )


@router.callback_query(F.data.startswith("bin:mass:"))
async def bin_mass(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.data is None or callback.message is None:
        return
    raw = callback.data.split(":", 2)[2]
    try:
        record = await _load(session, raw)
    except (BinUnavailable, ValueError):
        return
    gates = await _views(session)
    ranked = route(gates, record.iso2 or "", "auth")
    if not ranked:
        await callback.message.answer("🚧 No healthy auth gate for mass right now.")
        return
    gate = ranked[0]
    await state.set_state(Flow.await_file)
    await state.update_data(gate_id=gate.id, lane=gate.lane)
    await callback.message.edit_text(
        f"📁 <b>MASS · {render.esc(gate.label)}</b>\n\n"
        "Paste cards, one per line. Send /cancel to stop.\n"
        "<blockquote>Cost preview is shown after parsing.</blockquote>",
        reply_markup=kb.back_kb("hub:mass", "⬅️ Back"),
    )
