"""Unknown callback: never an error, always back to the menu (section 10.7)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.tg import keyboards as kb

router = Router(name="fallback")


@router.callback_query(F.data)
async def unknown(callback: CallbackQuery) -> None:
    if callback.message is not None:
        await callback.message.edit_text(
            "🏠 <b>MAIN MENU</b>\n\n<i>That button expired. Pick a lane.</i>",
            reply_markup=kb.main_menu(),
        )
