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
from handlers.common import claim_usage, parse_bin_arg
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
                f"❌ No BIN found for <code>{raw}</code>",
                parse_mode="HTML",
                reply_markup=ui.dashboard_kb(False, runtime.config.miniapp_base),
            )
            return
        # Show first result
        await perform_bin_lookup(message, runtime, rows[0]["bin"])
        return
    # 6 or 8 digit BIN
    await perform_bin_lookup(message, runtime, arg)


@router.message(Command("search"))
async def search_cmd(message: Message, command: CommandObject, runtime) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "Usage: /search <bank|brand|country>", reply_markup=ui.menu_only_kb()
        )
        return
    user = message.from_user
    await runtime.db.ensure_user(
        user.id, username=user.username or "", first_name=user.first_name or ""
    )
    db_user = await runtime.db.reset_quota_if_needed(user.id)
    rows = await runtime.db.search_bins(query)
    if not rows:
        await message.answer(
            "No matches in the permanent cache.", reply_markup=ui.menu_only_kb()
        )
        return
    # A result list exposes BIN metadata, so it costs one BIN lookup.
    decision = await claim_usage(runtime.db, db_user, "bin", runtime.config)
    if not decision["allowed"]:
        await message.answer(
            ui.paywall_text(), parse_mode="HTML", reply_markup=ui.paywall_kb()
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
            is_personal=True,
        )
        return

    if len(arg) == 6:
        # Do not charge on the 6th digit: the user is still typing.
        await query.answer(
            [
                InlineQueryResultArticle(
                    id=f"need8-{arg}",
                    title=f"BIN {arg} — add 2 digits",
                    description="Type the full 8-digit BIN, then pick the result.",
                    input_message_content=InputTextMessageContent(
                        message_text="SMARTBIN: type the full 8-digit BIN."
                    ),
                )
            ],
            cache_time=5,
            is_personal=True,
        )
        return

    user = query.from_user
    await runtime.db.ensure_user(
        user.id, username=user.username or "", first_name=user.first_name or ""
    )
    db_user = await runtime.db.reset_quota_if_needed(user.id)
    record = await runtime.store.get(arg)
    if record is None:
        await query.answer(
            [
                InlineQueryResultArticle(
                    id=f"missing-{arg}",
                    title=f"BIN {arg} not cached",
                    description="Open @SmartBlN_Bot and send the BIN to fetch it.",
                    input_message_content=InputTextMessageContent(
                        message_text=f"BIN {arg}: not in cache yet."
                    ),
                )
            ],
            cache_time=5,
            is_personal=True,
        )
        return

    # Inline mode re-sends the same query while the user scrolls; charge at
    # most once per BIN per user per day.
    first_today = await runtime.db.mark_daily_usage(user.id, "bin", arg)
    if first_today:
        decision = await claim_usage(runtime.db, db_user, "bin", runtime.config)
        if not decision["allowed"]:
            await runtime.db.unmark_daily_usage(user.id, "bin", arg)
            await query.answer(
                [
                    InlineQueryResultArticle(
                        id="paywall",
                        title="Daily free limit reached",
                        description="Open the bot to buy credits or premium.",
                        input_message_content=InputTextMessageContent(
                            message_text=(
                                "SMARTBIN daily free limit reached. "
                                "Open @SmartBlN_Bot to buy credits or premium."
                            )
                        ),
                    )
                ],
                cache_time=5,
                is_personal=True,
            )
            return

    await query.answer(
        [
            InlineQueryResultArticle(
                id=f"bin-{arg}",
                title=f"BIN {arg}",
                description=f"{record.brand} {record.level} {record.bank}",
                input_message_content=InputTextMessageContent(
                    message_text=card_block(record, free=True),
                    parse_mode="HTML",
                ),
            )
        ],
        cache_time=30,
        is_personal=True,
    )


@router.callback_query(F.data == "paywall")
async def paywall_callback(callback: CallbackQuery, runtime) -> None:
    await callback.message.answer(
        ui.paywall_text(), parse_mode="HTML", reply_markup=ui.paywall_kb()
    )
    await callback.answer()
