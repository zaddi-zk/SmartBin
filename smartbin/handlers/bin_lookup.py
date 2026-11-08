from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)

import ui
from handlers.actions import perform_bin_lookup, send_cached_bin
from handlers.common import parse_bin_arg
from models import Card
from render import card_block
from states import Flow

router = Router(name="bin_lookup")


@router.message(Command("bin"))
async def bin_cmd(
    message: Message, command: CommandObject, state: FSMContext, runtime
) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await state.set_state(Flow.bin)
        await message.answer(
            ui.bin_prompt(), parse_mode="HTML", reply_markup=ui.cancel_kb()
        )
        return
    arg = parse_bin_arg(raw)
    if arg is None:
        rows = await runtime.db.search_bins(raw)
        if not rows:
            await message.answer(
                "No matching BINs in cache. Send 6 or 8 digits.",
                reply_markup=ui.cancel_kb(),
            )
            return
        lines = [
            f"{row['bin']}  {row['brand']:<10} {row['level']:<10} "
            f"{row['bank'][:28]:<28} {row['country']}"
            for row in rows
        ]
        await message.answer(
            "<pre>" + "\n".join(lines) + "</pre>",
            parse_mode="HTML",
            reply_markup=ui.menu_only_kb(),
        )
        return
    await perform_bin_lookup(message, runtime, arg)


@router.message(Command("search"))
async def search_cmd(message: Message, command: CommandObject, runtime) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "Usage: /search <bank|brand|country>", reply_markup=ui.menu_only_kb()
        )
        return
    rows = await runtime.db.search_bins(query)
    if not rows:
        await message.answer(
            "No matches in the permanent cache.", reply_markup=ui.menu_only_kb()
        )
        return
    lines = [
        f"{r['bin']}  {r['brand']:<10} {r['level']:<10} "
        f"{r['bank'][:28]:<28} {r['country']}"
        for r in rows
    ]
    await message.answer(
        "<pre>" + "\n".join(lines) + "</pre>",
        parse_mode="HTML",
        reply_markup=ui.menu_only_kb(),
    )


@router.inline_query()
async def inline_bin(query: InlineQuery, runtime) -> None:
    arg = parse_bin_arg(query.query)
    if arg is None:
        await query.answer(
            [
                InlineQueryResultArticle(
                    id="help",
                    title="SMARTBIN: send 6 or 8 digits",
                    description="Example: 45717360",
                    input_message_content=InputTextMessageContent(
                        message_text="SMARTBIN: send 6 or 8 digits, e.g. 45717360"
                    ),
                )
            ],
            cache_time=5,
            is_personal=False,
        )
        return
    record = await runtime.store.get(arg) if len(arg) == 8 else await runtime.store.get_prefix(arg)
    await query.answer(
        [
            InlineQueryResultArticle(
                id=f"bin-{arg}",
                title=f"BIN {arg}",
                description=f"{record.brand} {record.level} {record.bank}",
                input_message_content=InputTextMessageContent(
                    message_text=card_block(record, free=True), parse_mode="HTML"
                ),
            )
        ],
        cache_time=30,
        is_personal=False,
    )


@router.callback_query(F.data == "paywall")
async def paywall_callback(callback: CallbackQuery, runtime) -> None:
    await callback.message.answer(
        ui.paywall_text(), parse_mode="HTML", reply_markup=ui.paywall_kb()
    )
    await callback.answer()
