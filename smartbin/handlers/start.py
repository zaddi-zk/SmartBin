from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

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


@router.message(F.text == "📱 Mini App")
async def open_miniapp(message: Message, state: FSMContext, runtime) -> None:
    """Open the Mini App."""
    await state.clear()
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Launch Mini App", web_app=WebAppInfo(url=runtime.config.miniapp_base))],
        [InlineKeyboardButton(text="✖ Close", callback_data="flow:menu")]
    ])
    await message.answer(
        "<b>📱 SMARTBIN Mini App</b>\n"
        "<blockquote>Advanced BIN lookup, real-time status, and your referral link.\n\n"
        "Tap below to launch!</blockquote>",
        reply_markup=kb
    )


@router.message(F.text == ui.BTN_REFER)
async def refer(message: Message, state: FSMContext, runtime) -> None:
    """Show referral link with 5-check bonus for both parties."""
    await state.clear()
    text, kb = await _refer_payload(runtime, message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "flow:refer")
async def refer_callback(
    callback: CallbackQuery, state: FSMContext, runtime
) -> None:
    """Inline Refer button: same payload as the reply-keyboard button."""
    await state.clear()
    text, kb = await _refer_payload(runtime, callback.from_user.id)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(text, reply_markup=kb)


async def _refer_payload(runtime, user_id: int):
    bot_username = (await runtime.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    text = (
        "<b>👥 Invite a friend, get rewarded!</b>\n"
        "<blockquote>🎁 You both get <b>5 free checks</b> when they join!\n\n"
        "Just share your link below 👇</blockquote>"
    )
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CopyTextButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Copy My Link", copy_text=CopyTextButton(text=ref_link))],
        [InlineKeyboardButton(text="📱 Open Mini App", web_app={"url": runtime.config.miniapp_base})],
        [InlineKeyboardButton(text="🏠 Back to Menu", callback_data="flow:menu")]
    ])
    return text, kb


@router.message(F.text == ui.BTN_ADMIN)
async def admin_panel(message: Message, state: FSMContext, runtime) -> None:
    if not is_admin(message.from_user.id, runtime.config):
        await message.answer("Admin only")
        return
    await state.clear()
    await message.answer(await dashboard(runtime), reply_markup=home_markup())
