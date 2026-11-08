from __future__ import annotations

import re
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import ui
from handlers.actions import perform_bin_lookup, run_check_and_render, send_cached_bin, send_menu
from handlers.admin import dashboard, home_markup
from handlers.bulk import begin_bulk, run_lines
from handlers.common import is_admin, parse_bin_arg
from handlers.pay import send_store
from handlers.status import queue_correction, send_status, send_why
from models import Card
from states import Flow

router = Router(name="flows")


def _field_from_text(text: str) -> str | None:
    key = text.strip().lower().replace(" ", "_")
    aliases = {
        "length": "card_length",
        "card_length": "card_length",
        "card": "card_length",
        "prepaid": "prepaid",
        "commercial": "commercial",
        "luhn": "luhn",
        "avs": "avs",
        "iso": "iso2",
        "iso2": "iso2",
    }
    if key in ui.FIELD_LABELS:
        return key
    return aliases.get(key)


# ---------------------------------------------------------------------------
# Callback actions (inline buttons)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "flow:menu")
async def cb_menu(callback: CallbackQuery, runtime) -> None:
    await send_menu(callback.message, runtime, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "flow:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Cancelled", show_alert=True)


@router.callback_query(F.data == "flow:bin")
async def cb_bin(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Flow.bin)
    await callback.message.answer(
        ui.bin_prompt(), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "flow:check")
async def cb_check(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Flow.check)
    await callback.message.answer(
        ui.check_prompt(), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("flow:checkbin:"))
async def cb_checkbin(callback: CallbackQuery, state: FSMContext) -> None:
    bin_arg = (callback.data or "").split(":", 2)[-1]
    if not bin_arg:
        await callback.answer("BIN missing", show_alert=True)
        return
    await state.set_state(Flow.check)
    await state.update_data(bin_arg=bin_arg)
    await callback.message.answer(
        ui.check_prompt(bin_arg), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("flow:binof:"))
async def cb_binof(callback: CallbackQuery, runtime) -> None:
    bin_arg = (callback.data or "").split(":", 2)[-1]
    if not bin_arg:
        await callback.answer("BIN missing", show_alert=True)
        return
    await send_cached_bin(callback.message, runtime, bin_arg, callback.from_user)
    await callback.answer()


@router.callback_query(F.data.startswith("flow:why:"))
async def cb_why(callback: CallbackQuery, runtime) -> None:
    bin_arg = (callback.data or "").split(":", 2)[-1]
    if not bin_arg:
        await callback.answer("BIN missing", show_alert=True)
        return
    await send_why(callback.message, runtime, bin_arg, callback.from_user)
    await callback.answer()


@router.callback_query(F.data.startswith("flow:fix:"))
async def cb_fix(callback: CallbackQuery, state: FSMContext) -> None:
    bin_arg = (callback.data or "").split(":", 2)[-1]
    if not bin_arg:
        await callback.answer("BIN missing", show_alert=True)
        return
    await state.set_state(Flow.correct_pick)
    await state.update_data(bin_arg=bin_arg)
    await callback.message.answer(
        ui.correct_fields_text(bin_arg),
        parse_mode="HTML",
        reply_markup=ui.correct_fields_kb(bin_arg),
    )
    await callback.answer()


@router.callback_query(F.data == "flow:bulk")
async def cb_bulk(callback: CallbackQuery, state: FSMContext, runtime) -> None:
    await begin_bulk(callback.message, runtime, state, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "flow:status")
async def cb_status(callback: CallbackQuery, runtime) -> None:
    await send_status(callback.message, runtime, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "flow:refresh")
async def cb_refresh(callback: CallbackQuery, runtime) -> None:
    await send_status(callback.message, runtime, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "flow:premium")
async def cb_premium(callback: CallbackQuery, runtime) -> None:
    await send_store(callback.message, runtime, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "flow:play")
async def cb_play(callback: CallbackQuery) -> None:
    await callback.message.answer_dice(emoji="🎲")
    await callback.answer()


@router.callback_query(F.data == "flow:admin")
async def cb_admin(callback: CallbackQuery, runtime) -> None:
    if not is_admin(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    await callback.message.answer(await dashboard(runtime), reply_markup=home_markup())
    await callback.answer()


@router.callback_query(F.data == "flow:runcheck")
async def cb_runcheck(callback: CallbackQuery, state: FSMContext, runtime) -> None:
    data = await state.get_data()
    raw = data.get("check_raw", "")
    if not raw:
        await callback.answer("No card to check", show_alert=True)
        return
    await state.clear()
    await run_check_and_render(callback.message, runtime, raw, user=callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "flow:runbulk")
async def cb_runbulk(callback: CallbackQuery, state: FSMContext, runtime) -> None:
    data = await state.get_data()
    raw = data.get("bulk_raw", "")
    cap = int(data.get("cap", runtime.config.bulk_free_lines))
    if not raw:
        await callback.answer("No cards to process", show_alert=True)
        return
    await state.clear()
    await run_lines(callback.message, runtime, raw, cap, callback.from_user)
    await callback.answer()


@router.callback_query(F.data.startswith("flow:corrfield:"))
async def cb_corrfield(callback: CallbackQuery, state: FSMContext) -> None:
    field = (callback.data or "").split(":", 2)[-1]
    data = await state.get_data()
    bin_arg = data.get("bin_arg", "")
    if not field or not bin_arg:
        await callback.answer("Field or BIN missing", show_alert=True)
        return
    await state.set_state(Flow.correct_value)
    await state.update_data(bin_arg=bin_arg, field=field)
    await callback.message.answer(
        ui.correct_field_prompt(field), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Reply-keyboard entry points
# ---------------------------------------------------------------------------


@router.message(F.text == ui.BTN_BIN)
async def kb_bin(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.bin)
    await message.answer(
        ui.bin_prompt(), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )


@router.message(F.text == ui.BTN_CHECK)
async def kb_check(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.check)
    await message.answer(
        ui.check_prompt(), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )


@router.message(F.text == ui.BTN_BULK)
async def kb_bulk(message: Message, state: FSMContext, runtime) -> None:
    await begin_bulk(message, runtime, state)


@router.message(F.text == ui.BTN_STATUS)
async def kb_status(message: Message, runtime) -> None:
    await send_status(message, runtime)


@router.message(F.text == ui.BTN_FIX)
async def kb_fix(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.correct_bin)
    await message.answer(
        ui.correct_bin_prompt(), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )


@router.message(F.text == ui.BTN_PREMIUM)
async def kb_premium(message: Message, runtime) -> None:
    await send_store(message, runtime)


# ---------------------------------------------------------------------------
# FSM input steps
# ---------------------------------------------------------------------------


@router.message(Flow.bin)
async def flow_bin_input(message: Message, state: FSMContext, runtime) -> None:
    arg = parse_bin_arg(message.text or "")
    if not arg:
        await message.answer(
            ui.bin_invalid(), parse_mode="HTML", reply_markup=ui.cancel_kb()
        )
        return
    await state.clear()
    await perform_bin_lookup(message, runtime, arg, message.from_user)


@router.message(Flow.check)
async def flow_check_input(message: Message, state: FSMContext, runtime) -> None:
    raw = (message.text or "").strip()
    try:
        card = Card.parse(raw)
    except ValueError as exc:
        await message.answer(
            ui.check_invalid(str(exc)), parse_mode="HTML", reply_markup=ui.cancel_kb()
        )
        return
    data = await state.get_data()
    prefix = data.get("bin_arg")
    if prefix and not card.number.startswith(prefix):
        await message.answer(
            ui.check_prefix_mismatch(prefix),
            parse_mode="HTML",
            reply_markup=ui.cancel_kb(),
        )
        return
    await state.set_state(Flow.check_confirm)
    await state.update_data(check_raw=raw)
    await message.answer(
        ui.confirm_check_text(card, runtime.config),
        parse_mode="HTML",
        reply_markup=ui.confirm_check_kb(),
    )


@router.message(Flow.check_confirm)
async def flow_check_confirm_input(
    message: Message, state: FSMContext, runtime
) -> None:
    raw = (message.text or "").strip()
    try:
        card = Card.parse(raw)
    except ValueError:
        card = None
    if card is not None:
        # A new card replaces the pending one and re-confirms.
        await state.update_data(check_raw=raw)
        await message.answer(
            ui.confirm_check_text(card, runtime.config),
            parse_mode="HTML",
            reply_markup=ui.confirm_check_kb(),
        )
        return
    if raw.lower() in {"yes", "y", "run", "check", "ok"}:
        data = await state.get_data()
        pending = data.get("check_raw", "")
        if not pending:
            await state.clear()
            await message.answer("No card to check", reply_markup=ui.menu_only_kb())
            return
        await state.clear()
        await run_check_and_render(message, runtime, pending, message.from_user)
        return
    await state.clear()
    await message.answer("Cancelled", reply_markup=ui.menu_only_kb())


@router.message(Flow.correct_bin)
async def flow_correct_bin_input(message: Message, state: FSMContext) -> None:
    arg = parse_bin_arg(message.text or "")
    if not arg:
        await message.answer(
            ui.bin_invalid(), parse_mode="HTML", reply_markup=ui.cancel_kb()
        )
        return
    await state.update_data(bin_arg=arg)
    await state.set_state(Flow.correct_pick)
    await message.answer(
        ui.correct_fields_text(arg),
        parse_mode="HTML",
        reply_markup=ui.correct_fields_kb(arg),
    )


@router.message(Flow.correct_pick)
async def flow_correct_pick_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    bin_arg = data.get("bin_arg", "")
    field = _field_from_text(message.text or "")
    if not field:
        await message.answer(
            "Choose a field with the buttons, or type its name.",
            reply_markup=ui.correct_fields_kb(bin_arg),
        )
        return
    await state.set_state(Flow.correct_value)
    await state.update_data(bin_arg=bin_arg, field=field)
    await message.answer(
        ui.correct_field_prompt(field), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )


@router.message(Flow.correct_value)
async def flow_correct_value_input(
    message: Message, state: FSMContext, runtime
) -> None:
    value = (message.text or "").strip()
    if not value:
        await message.answer("Value cannot be empty", reply_markup=ui.cancel_kb())
        return
    data = await state.get_data()
    bin_arg = data.get("bin_arg", "")
    field = data.get("field", "")
    if not bin_arg or not field:
        await state.clear()
        await message.answer("BIN or field missing", reply_markup=ui.menu_only_kb())
        return
    await state.clear()
    await queue_correction(message, runtime, bin_arg, field, value)


@router.message(Flow.bulk_input)
async def flow_bulk_input(message: Message, state: FSMContext, runtime) -> None:
    raw = (message.text or "").strip()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        await message.answer("No cards found", reply_markup=ui.cancel_kb())
        return
    data = await state.get_data()
    cap = int(data.get("cap", runtime.config.bulk_free_lines))
    if len(lines) > cap:
        await message.answer(
            f"Too many lines ({len(lines)} > {cap}). Send up to {cap}.",
            reply_markup=ui.cancel_kb(),
        )
        return
    await state.set_state(Flow.bulk_confirm)
    await state.update_data(bulk_raw=raw)
    await message.answer(
        ui.bulk_confirm_text(len(lines)),
        parse_mode="HTML",
        reply_markup=ui.bulk_confirm_kb(),
    )


@router.message(Flow.bulk_confirm)
async def flow_bulk_confirm_input(
    message: Message, state: FSMContext, runtime
) -> None:
    raw = (message.text or "").strip()
    if raw.lower() in {"yes", "y", "run", "ok"}:
        data = await state.get_data()
        pending = data.get("bulk_raw", "")
        cap = int(data.get("cap", runtime.config.bulk_free_lines))
        await state.clear()
        if not pending:
            await message.answer("No cards to process", reply_markup=ui.menu_only_kb())
            return
        await run_lines(message, runtime, pending, cap, message.from_user)
        return
    # New paste replaces the pending batch and re-confirms.
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) >= 2:
        data = await state.get_data()
        cap = int(data.get("cap", runtime.config.bulk_free_lines))
        if len(lines) <= cap:
            await state.update_data(bulk_raw=raw)
            await message.answer(
                ui.bulk_confirm_text(len(lines)),
                parse_mode="HTML",
                reply_markup=ui.bulk_confirm_kb(),
            )
            return
    await state.clear()
    await message.answer("Cancelled", reply_markup=ui.menu_only_kb())


# ---------------------------------------------------------------------------
# Idle text detection. Registered last so state handlers win.
# ---------------------------------------------------------------------------


@router.message(F.text)
async def idle_text(message: Message, state: FSMContext, runtime) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    arg = parse_bin_arg(text)
    if arg:
        await perform_bin_lookup(message, runtime, arg, message.from_user)
        return
    if re.search(r"\d{13,19}\s*\|", text):
        try:
            card = Card.parse(text)
        except ValueError as exc:
            await message.answer(
                ui.check_invalid(str(exc)),
                parse_mode="HTML",
                reply_markup=ui.cancel_kb(),
            )
            return
        await state.set_state(Flow.check_confirm)
        await state.update_data(check_raw=text)
        await message.answer(
            ui.confirm_check_text(card, runtime.config),
            parse_mode="HTML",
            reply_markup=ui.confirm_check_kb(),
        )
        return
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and any("|" in line for line in lines):
        cap = runtime.config.bulk_free_lines
        await state.set_state(Flow.bulk_confirm)
        await state.update_data(bulk_raw=text, cap=cap)
        await message.answer(
            ui.bulk_confirm_text(len(lines)),
            parse_mode="HTML",
            reply_markup=ui.bulk_confirm_kb(),
        )
        return
    if re.fullmatch(r"\d{13,19}", text):
        await message.answer(
            "Send the expiry and CVV too, as <code>number|MM|YY|CVV</code>.",
            parse_mode="HTML",
            reply_markup=ui.cancel_kb(),
        )
        return
    await message.answer(
        ui.unknown_text(), parse_mode="HTML", reply_markup=ui.menu_only_kb()
    )
