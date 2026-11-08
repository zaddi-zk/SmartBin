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
    user_id = message.from_user.id
    source_tag = command.args or ""
    
    # Check for referral
    referrer_id = None
    if source_tag and source_tag.startswith("ref_"):
        try:
            referrer_id = int(source_tag.split("_", 1)[1])
        except (ValueError, IndexError):
            pass
    
    await runtime.db.ensure_user(
        user_id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        source_tag=source_tag,
    )
    
    # Grant 5 free checks to both referrer and referee
    if referrer_id and referrer_id != user_id:
        await runtime.db.grant_referral_bonus(user_id, referrer_id)
    
    await runtime.db.stats_bump("start")
    user = await runtime.db.reset_quota_if_needed(user_id)
    if int(user["free_bin"]) == 0 and int(user["free_check"]) == 0:
        await runtime.db.stats_bump("funnel:start")
    await send_menu(message, runtime)


@router.message(Command("menu"))
async def menu(message: Message, runtime) -> None:
    await send_menu(message, runtime)


@router.message(F.text == ui.BTN_REFER)
async def refer(message: Message, state: FSMContext, runtime) -> None:
    """Show referral link with 5-check bonus for both parties."""
    await state.clear()
    user_id = message.from_user.id
    bot_username = (await runtime.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    text = (
        "<b>👥 Refer friends</b>\n"
        "<blockquote>Share your link. When someone joins via your link, you both get 5 free checks.</blockquote>\n"
        f"<pre>{ref_link}</pre>\n"
        "<i>Tap Copy to share</i>"
    )
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CopyTextButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Copy Link", copy_text=CopyTextButton(text=ref_link))]
    ])
    await message.answer(text, reply_markup=kb)


@router.message(F.text == ui.BTN_ADMIN)
async def admin_panel(message: Message, state: FSMContext, runtime) -> None:
    if not is_admin(message.from_user.id, runtime.config):
        await message.answer("Admin only")
        return
    await state.clear()
    await message.answer(await dashboard(runtime), reply_markup=home_markup())
