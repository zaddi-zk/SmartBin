from __future__ import annotations

import asyncio
import csv
import io
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message

import ui
from handlers.common import (
    claim_usage,
    free_watermark,
    is_admin,
    is_premium,
    is_unlimited,
    refund_usage,
    remaining,
)
from models import Card
from render import WATERMARK
from states import Flow

router = Router(name="bulk")


async def bulk_cap(runtime, user) -> int:
    """Hard cap for one paste, clamped to what the account can actually pay."""
    if is_unlimited(user, runtime.config):
        return runtime.config.bulk_premium_lines
    try:
        uid = int(user["user_id"])
    except (KeyError, TypeError, ValueError):
        uid = int(getattr(user, "id", 0) or 0)
    if is_admin(uid, runtime.config) or is_premium(user):
        return runtime.config.bulk_premium_lines
    hard = 500 if int(user["credits"]) >= 1 else runtime.config.bulk_free_lines
    left = remaining(user, "check", runtime.config) or 0
    return max(0, min(hard, left))


async def begin_bulk(message: Message, runtime, state: FSMContext, user=None) -> None:
    user = user or message.from_user
    await runtime.db.ensure_user(
        user.id,
        username=getattr(user, "username", "") or "",
        first_name=getattr(user, "first_name", "") or "",
    )
    db_user = await runtime.db.reset_quota_if_needed(user.id)
    cap = await bulk_cap(runtime, db_user)
    if cap <= 0:
        await message.answer(
            ui.paywall_text(), parse_mode="HTML", reply_markup=ui.paywall_kb()
        )
        return
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
    db_user = await runtime.db.reset_quota_if_needed(user.id)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    selected = lines[:cap]
    if not selected:
        await message.answer("No cards found.", reply_markup=ui.bulk_kb())
        return

    # BulkB runs real auth checks: every line must be paid for exactly like a
    # single check (free allowance first, then credits). Premium is exempt.
    decisions: list[dict[str, Any]] = []
    if is_unlimited(db_user, runtime.config):
        decisions = [{"consume": "premium"} for _ in selected]
    else:
        for _ in selected:
            decision = await claim_usage(runtime.db, db_user, "check", runtime.config)
            if not decision["allowed"]:
                break
            decisions.append(decision)
        if not decisions:
            await message.answer(
                ui.paywall_text(), parse_mode="HTML", reply_markup=ui.paywall_kb()
            )
            return
        selected = selected[: len(decisions)]

    granted = len(decisions)
    skipped = len(lines[:cap]) - granted
    sem = asyncio.Semaphore(5)

    async def one(line: str):
        async with sem:
            try:
                return line, await runtime.runner.run(user.id, line), None
            except Exception as exc:  # one bad line must not sink the batch
                return line, None, exc

    raw_results = await asyncio.gather(*(one(line) for line in selected))
    results = []
    failed = 0
    for (line, outcome, exc), decision in zip(raw_results, decisions):
        if outcome is None:
            # A line that never produced a verdict is not charged.
            await refund_usage(runtime.db, user.id, "check", decision["consume"])
            failed += 1
            continue
        results.append((line, outcome))

    if not results:
        await message.answer(
            "⚠️ No line completed. No checks were used.",
            reply_markup=ui.bulk_kb(),
        )
        return

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
    free = free_watermark(db_user, runtime.config)
    body = "<pre>" + "\n".join(summary) + "</pre>"
    if skipped:
        body += (
            f"\n⚠️ {skipped} line(s) skipped: daily free check limit reached. "
            "Buy credits to run the rest."
        )
    if failed:
        body += f"\n⚠️ {failed} line(s) failed to run; no quota was used for them."
    if free:
        body += "\n" + WATERMARK
    await message.answer(body, parse_mode="HTML", reply_markup=ui.bulk_kb())
    await message.answer_document(
        BufferedInputFile(buf.getvalue().encode("utf-8"), filename="smartbin_bulk.csv"),
        caption=f"SMARTBIN bulk: {len(results)} rows",
    )
