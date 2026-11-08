from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import ui
from models import Card
from states import Flow

router = Router(name="check")


@router.message(Command("check"))
async def check_cmd(
    message: Message, command: CommandObject, state: FSMContext, runtime
) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await state.set_state(Flow.check)
        await message.answer(
            ui.check_prompt(), parse_mode="HTML", reply_markup=ui.cancel_kb()
        )
        return
    try:
        card = Card.parse(raw)
    except ValueError as exc:
        await message.answer(
            ui.check_invalid(str(exc)), parse_mode="HTML", reply_markup=ui.cancel_kb()
        )
        return
    await state.set_state(Flow.check_confirm)
    await state.update_data(check_raw=raw)
    await message.answer(
        ui.confirm_check_text(card, runtime.config),
        parse_mode="HTML",
        reply_markup=ui.confirm_check_kb(),
    )
