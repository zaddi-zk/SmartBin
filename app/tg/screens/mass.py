"""Mass check: paste or drop a file, run sequentially, export the result."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.parse import Card, CardError, fmt_cr
from app.db import repo
from app.db.models import User
from app.runtime import runtime
from app.tg import keyboards as kb
from app.tg import render
from app.tg.guards import load_user
from app.tg.states import Flow

router = Router(name="mass")

MAX_LINES = 50


@router.callback_query(F.data == "hub:mass")
async def mass_home(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, callback, bot=bot, settings=settings)
    if user is None or callback.message is None:
        return
    await callback.message.edit_text(
        "📁 <b>MASS CHECK</b>\n\n"
        "Paste one card per line, or drop a .txt file.\n"
        f"<blockquote>Up to {MAX_LINES} lines. Every line is parsed with the same "
        "flexible parser as a single check.</blockquote>",
        reply_markup=kb.make_kb(
            [kb.Button(text="✍️ Paste list", callback_data="mass:paste")],
            [kb.Button(text="📄 Drop file", callback_data="mass:file")],
            [kb.back("hub:main", "⬅️ Menu")],
        ),
    )


@router.callback_query(F.data == "mass:paste")
async def mass_paste(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(Flow.await_file)
    await state.update_data(mode="paste")
    await callback.message.edit_text(
        "✍️ <b>PASTE LIST</b>\nSend the cards now, one per line.",
        reply_markup=kb.back_kb("hub:mass", "⬅️ Back"),
    )


@router.callback_query(F.data == "mass:file")
async def mass_file(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(Flow.await_file)
    await state.update_data(mode="file")
    await callback.message.edit_text(
        "📄 <b>DROP FILE</b>\nSend a .txt file.",
        reply_markup=kb.back_kb("hub:mass", "⬅️ Back"),
    )


@router.message(Flow.await_file, F.document)
async def mass_document(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, message, bot=bot, settings=settings)
    if user is None or message.document is None or bot is None:
        return
    file = await bot.download(message.document)
    if file is None:
        await message.answer("Could not read that file.")
        return
    raw = file.read()
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
    await run_mass(message, session, state, user, text)


@router.message(Flow.await_file, F.text)
async def mass_text(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, message, bot=bot, settings=settings)
    if user is None or message.text is None:
        return
    await run_mass(message, session, state, user, message.text)


async def run_mass(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    text: str,
) -> None:
    data = await state.get_data()
    gate_id = str(data.get("gate_id") or user.sticky_gate or "")
    lane = str(data.get("lane") or "auth")
    if not gate_id:
        gates = await repo.list_gates(session, lane)
        gate_id = next((gate.id for gate in gates if gate.enabled), "")
    gate = await repo.get_gate(session, gate_id) if gate_id else None
    if gate is None:
        await message.answer("🚧 No gate available for mass.")
        return

    cards: list[Card] = []
    seen: set[str] = set()
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            card = Card.parse(line)
        except CardError:
            skipped += 1
            continue
        if card.number in seen:
            continue
        seen.add(card.number)
        cards.append(card)
        if len(cards) >= MAX_LINES:
            break

    if not cards:
        await message.answer("⚠️ No valid cards in that list.")
        return

    quote = Decimal(gate.cost_cr) * len(cards)
    if Decimal(user.credits) < Decimal(gate.cost_cr):
        await message.answer(
            f"💠 <b>{fmt_cr(quote)} cr quoted</b>\n"
            "<blockquote>"
            f"Balance {fmt_cr(Decimal(user.credits))} cr. Nothing dispatched."
            "</blockquote>",
            reply_markup=kb.plans_kb(),
        )
        return

    progress = await message.answer(
        f"📊 <b>MASS · {render.esc(gate.label)}</b>\n0/{len(cards)} · quote {fmt_cr(quote)} cr"
    )
    await state.update_data(stop=False)

    counts: dict[str, int] = {}
    approved: list[str] = []
    spent = Decimal("0")
    for index, card in enumerate(cards, start=1):
        current = await state.get_data()
        if current.get("stop"):
            break
        try:
            outcome = await runtime().executor.run(
                session,
                user=user,
                card=card,
                lane=lane,
                gate_id=gate.id,
            )
        except Exception:
            counts["ERROR"] = counts.get("ERROR", 0) + 1
            continue
        counts[outcome.verdict.value] = counts.get(outcome.verdict.value, 0) + 1
        spent += outcome.debited
        if outcome.verdict.value in ("APPROVED", "LIVE", "LIVE_3DS"):
            approved.append(render.card_line(card))
        if index % 5 == 0 or index == len(cards):
            await progress.edit_text(
                f"📊 <b>MASS · {render.esc(gate.label)}</b>\n"
                f"{index}/{len(cards)} · "
                + " · ".join(f"{name} {count}" for name, count in sorted(counts.items()))
            )

    summary = [
        "🧾 <b>MASS SUMMARY</b>",
        "",
        f"checked <b>{sum(counts.values())}</b> · spent <b>{fmt_cr(spent)} cr</b>",
    ]
    summary.extend(f"{name}: <b>{count}</b>" for name, count in sorted(counts.items()))
    if skipped:
        summary.append(f"skipped bad lines: {skipped}")
    if approved:
        summary.append("")
        summary.append("<pre>" + render.esc("\n".join(approved)) + "</pre>")
    await state.clear()
    await message.answer(
        "\n".join(summary),
        reply_markup=kb.make_kb(
            [kb.Button(text="🟢 Export approved .txt", callback_data="mass:exp:ok")],
            [kb.Button(text="⬅️ Menu", callback_data="hub:main")],
        ),
    )
    # Keep the export payload after clearing the FSM state: the user is IDLE,
    # but the summary's export button still has something to send.
    await state.set_data({"mass_ok": approved})


@router.callback_query(F.data == "mass:stop")
async def mass_stop(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(stop=True)
    if callback.message is not None:
        await callback.message.answer("⏹ Stop requested.")


@router.callback_query(F.data == "mass:exp:ok")
async def mass_export(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    rows: Any = data.get("mass_ok") or []
    if callback.message is None:
        return
    if not rows:
        await callback.message.answer("Nothing to export.")
        return
    payload = "\n".join(str(row) for row in rows).encode()
    await callback.message.answer_document(
        BufferedInputFile(payload, filename="approved.txt"),
        caption=f"🟢 {len(rows)} approved",
    )
