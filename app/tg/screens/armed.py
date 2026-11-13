"""Armed state, card intake, dispatch, and result callbacks."""

from __future__ import annotations

from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.ledger import InsufficientCredits
from app.core.parse import Card, CardError, fmt_cr
from app.db import repo
from app.db.models import User
from app.exec.check import CheckOutcome
from app.routing.router import GateView, sibling
from app.runtime import runtime
from app.tg import keyboards as kb
from app.tg import render
from app.tg.guards import load_user
from app.tg.states import Flow

router = Router(name="armed")


async def armed_for(session: AsyncSession, gate_id: str) -> tuple[GateView, str] | None:
    gate = await repo.get_gate(session, gate_id)
    if gate is None:
        return None
    state = await repo.ensure_gate_state(session, gate.id)
    from app.routing.router import build_view

    view = build_view(gate, state)
    return view, render.armed_text(view)


async def dispatch(
    session: AsyncSession,
    user: User,
    card: Card,
    gate_id: str,
    lane: str,
) -> CheckOutcome:
    iso2 = ""
    try:
        info = await runtime().bins.resolve(session, card.number)
        iso2 = info.iso2
    except Exception:
        iso2 = ""
    return await runtime().executor.run(
        session,
        user=user,
        card=card,
        lane=lane,
        gate_id=gate_id,
        bin_iso2=iso2,
    )


async def render_outcome(
    session: AsyncSession, outcome: CheckOutcome, lane: str, raw_card: str
) -> tuple[str, Any]:
    views = await runtime().executor.views(session)
    sib = sibling(views, lane, outcome.gate_id)
    text = render.result_text(outcome, card_line=raw_card)
    return text, kb.result(outcome, sib.id if sib is not None else None)


@router.callback_query(F.data.startswith("run:"))
async def arm(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    if callback.data is None or callback.message is None:
        return
    user = await load_user(session, callback, bot=bot, settings=settings)
    if user is None:
        return
    gate_id = callback.data.split(":", 1)[1]
    gate = await repo.get_gate(session, gate_id)
    if gate is None:
        await callback.message.edit_text(render.hub_text(), reply_markup=kb.gate_hub())
        return
    if not gate.enabled or runtime().adapters.get(gate.processor) is None:
        await callback.message.edit_text(
            f"🚧 <b>{render.esc(gate.label)}</b> is not live yet.\n"
            "<blockquote>No adapter is installed for this processor, so no card "
            "can reach it. 0 cr charged.</blockquote>",
            reply_markup=kb.make_kb(
                [kb.Button(text="🔀 Nearest healthy", callback_data=f"gate:near:{gate.id}")],
                [kb.back(f"hub:{gate.lane}", "⬅️ Back")],
            ),
        )
        return
    await repo.set_sticky_gate(session, user.id, gate.id)
    await state.set_state(Flow.await_card)
    await state.update_data(gate_id=gate.id, lane=gate.lane)
    armed = await armed_for(session, gate.id)
    if armed is None:
        return
    view, text = armed
    await callback.message.edit_text(text, reply_markup=kb.armed(view))


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Cleared. /menu to start again.")


@router.message(Flow.await_card, F.text)
async def card_text(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, message, bot=bot, settings=settings)
    if user is None or message.text is None:
        return
    data = await state.get_data()
    gate_id = str(data.get("gate_id") or user.sticky_gate or "")
    lane = str(data.get("lane") or "auth")

    try:
        card = Card.parse(message.text)
    except CardError as exc:
        await message.answer(
            f"⚠️ <b>BAD FORMAT</b>\n<blockquote>{render.esc(exc)}</blockquote>\n"
            "Expected <code>cc|mm|yy|cvv</code> or <code>cc mm yy cvv</code>.",
            reply_markup=kb.back_kb("card:rearm", "⬅️ Try again"),
        )
        return

    await state.update_data(last_raw=render.card_line(card))
    duplicate = await repo.duplicate_check(
        session, user.id, card.pan_hash, settings.dup_window_s
    )
    if duplicate is not None:
        await message.answer(
            "🔁 <b>ALREADY CHECKED</b>\n"
            f"<blockquote>{render.esc(card.masked)} was checked "
            f"{render.seconds_ago(duplicate.created_at)} ago on "
            f"{render.esc(duplicate.gate_id)}: {render.esc(duplicate.verdict)}.</blockquote>",
            reply_markup=kb.make_kb(
                [kb.Button(text="🔁 Rerun anyway", callback_data="card:retry")],
                [kb.back("card:rearm", "⬅️ Back")],
            ),
        )
        return
    try:
        outcome = await dispatch(session, user, card, gate_id, lane)
    except InsufficientCredits as exc:
        await message.answer(
            f"💠 <b>{fmt_cr(exc.needed)} cr short</b>\n"
            f"<blockquote>Balance {fmt_cr(exc.balance)} cr. Nothing was dispatched.</blockquote>",
            reply_markup=kb.plans_kb(),
        )
        return

    text, markup = await render_outcome(session, outcome, lane, render.card_line(card))
    await message.answer(text, reply_markup=markup)
    await state.set_state(Flow.await_card)


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def idle_text(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    """Section 8: text in IDLE is sniffed — 6-8 digits is a BIN, else a card."""
    if message.text is None:
        return
    user = await load_user(session, message, bot=bot, settings=settings)
    if user is None:
        return
    raw = message.text.strip()
    if raw.isdigit() and 6 <= len(raw) <= 8:
        try:
            info = await runtime().bins.resolve(session, raw)
        except Exception:
            await message.answer("⚠️ BIN lookup is unavailable right now.")
            return
        views = await runtime().executor.views(session)
        best = [view.label for view in views if view.lane == "auth" and view.enabled][:2]
        await message.answer(
            render.bin_text(info, best),
            reply_markup=kb.bin_result(
                next(
                    (view.id for view in views if view.lane == "auth" and view.enabled),
                    None,
                )
            ),
        )
        return

    gate_id = user.sticky_gate or ""
    if not gate_id:
        await message.answer(render.hub_text(), reply_markup=kb.gate_hub())
        return
    gate = await repo.get_gate(session, gate_id)
    if gate is None:
        await message.answer(render.hub_text(), reply_markup=kb.gate_hub())
        return
    try:
        card = Card.parse(raw)
    except CardError as exc:
        await message.answer(f"⚠️ <b>BAD FORMAT</b>\n<blockquote>{render.esc(exc)}</blockquote>")
        return
    try:
        outcome = await dispatch(session, user, card, gate.id, gate.lane)
    except InsufficientCredits as exc:
        await message.answer(
            f"💠 <b>{fmt_cr(exc.needed)} cr short</b>",
            reply_markup=kb.plans_kb(),
        )
        return
    text, markup = await render_outcome(session, outcome, gate.lane, render.card_line(card))
    await message.answer(text, reply_markup=markup)
    await state.set_state(Flow.await_card)
    await state.update_data(gate_id=gate.id, lane=gate.lane, last_raw=render.card_line(card))


@router.callback_query(F.data == "card:hint")
async def hint(callback: CallbackQuery) -> None:
    if callback.message is not None:
        await callback.message.answer(
            "Paste one card. Any of these work:\n"
            "<code>4242424242424242|11|29|716</code>\n"
            "<code>4242 4242 4242 4242 11/29 716</code>\n"
            "<code>4242424242424242;11;2029;716;90210</code>"
        )


@router.callback_query(F.data == "card:rearm")
async def rearm(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        return
    data = await state.get_data()
    gate_id = str(data.get("gate_id") or "")
    armed = await armed_for(session, gate_id) if gate_id else None
    if armed is None:
        await callback.message.edit_text(render.hub_text(), reply_markup=kb.gate_hub())
        return
    view, text = armed
    await state.set_state(Flow.await_card)
    await callback.message.edit_text(text, reply_markup=kb.armed(view))


@router.callback_query(F.data == "card:copy")
async def copy(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    raw = str(data.get("last_raw") or "")
    if callback.message is None:
        return
    if not raw:
        await callback.message.answer("No card in this session. Paste it again.")
        return
    await callback.message.answer(f"<code>{render.esc(raw)}</code>")


@router.callback_query(F.data.in_({"card:retry", "card:next"}))
async def retry(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        return
    data = await state.get_data()
    raw = str(data.get("last_raw") or "")
    lane = str(data.get("lane") or "auth")
    gate_id = str(data.get("gate_id") or "")
    if not raw:
        await callback.message.answer("Nothing to retry. Paste the card again.")
        return
    card = Card.parse(raw)
    user = await repo.get_user(session, callback.from_user.id if callback.from_user else 0)
    if user is None:
        return
    if callback.data == "card:next":
        from app.routing.router import route

        candidates = [
            view
            for view in route(await runtime().executor.views(session), "", lane)
            if view.id != gate_id
        ]
        if not candidates:
            await callback.message.answer("No healthy sibling gate in this lane.")
            return
        gate_id = candidates[0].id
    try:
        outcome = await dispatch(session, user, card, gate_id, lane)
    except InsufficientCredits:
        await callback.message.answer("💠 Not enough credits.", reply_markup=kb.plans_kb())
        return
    await state.update_data(gate_id=gate_id)
    text, markup = await render_outcome(session, outcome, lane, raw)
    await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("card:sibling:"))
async def sibling_retry(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.data is None or callback.message is None:
        return
    data = await state.get_data()
    raw = str(data.get("last_raw") or "")
    lane = str(data.get("lane") or "auth")
    if not raw:
        return
    gate_id = callback.data.split(":", 2)[2]
    user = await repo.get_user(session, callback.from_user.id if callback.from_user else 0)
    if user is None:
        return
    try:
        outcome = await dispatch(session, user, Card.parse(raw), gate_id, lane)
    except InsufficientCredits:
        await callback.message.answer("💠 Not enough credits.", reply_markup=kb.plans_kb())
        return
    await state.update_data(gate_id=gate_id)
    text, markup = await render_outcome(session, outcome, lane, raw)
    await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "card:kill")
async def kill(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.message is None:
        return
    data = await state.get_data()
    raw = str(data.get("last_raw") or "")
    gate_id = str(data.get("gate_id") or "")
    if not raw:
        await callback.message.answer("Paste a card first.")
        return
    user = await repo.get_user(session, callback.from_user.id if callback.from_user else 0)
    if user is None:
        return
    card = Card.parse(raw)
    iso2 = ""
    try:
        iso2 = (await runtime().bins.resolve(session, card.number)).iso2
    except Exception:
        iso2 = ""
    try:
        outcome = await runtime().kill.run(
            session, user=user, card=card, gate_id=gate_id, bin_iso2=iso2
        )
    except InsufficientCredits:
        await callback.message.answer(
            "💠 Not enough credits for a kill run.", reply_markup=kb.plans_kb()
        )
        return
    await callback.message.answer(render.kill_text(outcome), reply_markup=kb.kill_result())
