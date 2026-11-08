from __future__ import annotations

import asyncio
import csv
import io

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message

import ui
from handlers.common import free_watermark, is_premium
from models import Card
from render import WATERMARK
from states import Flow

router = Router(name="bulk")


async def bulk_cap(runtime, user) -> int:
    if is_premium(user):
        return runtime.config.bulk_premium_lines
    if int(user["credits"]) >= 1:
        return 500
    return runtime.config.bulk_free_lines


async def begin_bulk(message: Message, runtime, state: FSMContext, user=None) -> None:
    user = user or message.from_user
    db_user = await runtime.db.reset_quota_if_needed(user.id)
    cap = await bulk_cap(runtime, db_user)
    await state.set_state(Flow.bulk_input)
    await state.update_data(cap=cap)
    await message.answer(
        ui.bulk_prompt(cap), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )


@router.message(Command("bulk"))
async def bulk_cmd(message: Message, state: FSMContext, runtime) -> None:
    await runtime.db.ensure_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )
    await begin_bulk(message, runtime, state)


@router.message(Command("cancel"))
async def bulk_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Cancelled", reply_markup=ui.menu_only_kb())


async def run_lines(
    message: Message, runtime, raw: str, cap: int, user=None
) -> None:
    user = user or message.from_user
    await runtime.db.ensure_user(
        user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    selected = lines[:cap]
    if not selected:
        await message.answer("No cards found.", reply_markup=ui.bulk_kb())
        return

    sem = asyncio.Semaphore(5)

    async def one(line: str):
        async with sem:
            return line, await runtime.runner.run(user.id, line)

    results = await asyncio.gather(*(one(line) for line in selected))

    rows = []
    summary = []
    for line, outcome in results:
        try:
            card = Card.parse(line)
            masked = card.masked()
            bin8 = card.bin8
        except ValueError:
            masked = line[:20]
            bin8 = ""
        rows.append(
            [
                masked,
                bin8,
                outcome.verdict,
                "YES" if outcome.live else "NO",
                outcome.action,
                outcome.raw_code or outcome.decline_code,
                outcome.gate,
                outcome.latency_ms,
                outcome.note,
            ]
        )
        summary.append(
            f"{bin8 or '-':<10} {outcome.verdict:<18} "
            f"{'LIVE' if outcome.live else 'DEAD':<4} {outcome.action}"
        )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["card", "bin", "verdict", "live", "action", "code", "gate", "latency_ms", "note"]
    )
    writer.writerows(rows)
    db_user = await runtime.db.reset_quota_if_needed(user.id)
    free = free_watermark(db_user)
    body = "<pre>" + "\n".join(summary) + "</pre>"
    if free:
        body += "\n" + WATERMARK
    await message.answer(body, parse_mode="HTML", reply_markup=ui.bulk_kb())
    await message.answer_document(
        BufferedInputFile(buf.getvalue().encode("utf-8"), filename="smartbin_bulk.csv"),
        caption=f"SMARTBIN bulk: {len(results)} rows",
    )
