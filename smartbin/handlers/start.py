from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import ui
from handlers.actions import send_menu
from handlers.admin import dashboard, home_markup
from handlers.common import is_admin

router = Router(name="start")


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, runtime) -> None:
    await runtime.db.ensure_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        source_tag=command.args or "",
    )
    await runtime.db.stats_bump("start")
    user = await runtime.db.reset_quota_if_needed(message.from_user.id)
    if int(user["free_bin"]) == 0 and int(user["free_check"]) == 0:
        await runtime.db.stats_bump("funnel:start")
    await send_menu(message, runtime)


@router.message(Command("menu"))
async def menu(message: Message, runtime) -> None:
    await send_menu(message, runtime)


@router.message(F.text == ui.BTN_PLAY)
async def play(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer_dice(emoji="🎲")


@router.message(F.text == ui.BTN_ADMIN)
async def admin_panel(message: Message, state: FSMContext, runtime) -> None:
    if not is_admin(message.from_user.id, runtime.config):
        await message.answer("Admin only")
        return
    await state.clear()
    await message.answer(await dashboard(runtime), reply_markup=home_markup())
