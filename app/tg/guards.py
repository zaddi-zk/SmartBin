"""Guard chain (section 6): ban -> membership -> tier -> credits -> rate -> debounce.

The middlewares handle rate and debounce. This module handles the identity
guards and renders their refusal screens.
"""

from __future__ import annotations

import time
from typing import Any

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.db.models import User

_member_cache: dict[tuple[int, int], float] = {}
_MEMBER_TTL = 60.0


async def refuse(
    event: CallbackQuery | Message, text: str, kb: InlineKeyboardMarkup | None = None
) -> None:
    if isinstance(event, CallbackQuery):
        if event.message is not None:
            await event.message.edit_text(text, reply_markup=kb)
        return
    await event.answer(text, reply_markup=kb)


async def banned_screen(event: CallbackQuery | Message) -> None:
    await refuse(event, "🚫 <b>BANNED</b>\n<blockquote>Access revoked.</blockquote>")


async def access_screen(event: CallbackQuery | Message) -> None:
    rows: list[list[InlineKeyboardButton]] = []
    if isinstance(event, CallbackQuery):
        rows.append([InlineKeyboardButton(text="✅ Verify", callback_data="me:verify")])
    await refuse(
        event,
        "🔒 <b>Access Restricted</b>\nJoin both, then Verify.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def is_member(bot: Any, chat_id: str, user_id: int) -> bool:
    if not chat_id:
        return True
    key = (hash(chat_id), user_id)
    now = time.monotonic()
    cached = _member_cache.get(key)
    if cached is not None and now - cached < _MEMBER_TTL:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    if member.status in ("member", "administrator", "creator"):
        _member_cache[key] = now
        return True
    return False


async def member_of_all(bot: Any, settings: Settings, user_id: int) -> bool:
    for chat in (settings.required_channel, settings.required_group):
        if chat and not await is_member(bot, chat, user_id):
            return False
    return True


async def load_user(
    session: AsyncSession,
    event: CallbackQuery | Message,
    *,
    bot: Any = None,
    settings: Settings | None = None,
) -> User | None:
    tg_user = event.from_user
    if tg_user is None:
        return None
    user = await repo.ensure_user(session, tg_user.id, tg_user.language_code or "en")
    if user.banned:
        await banned_screen(event)
        return None
    if (
        settings is not None
        and bot is not None
        and not await member_of_all(bot, settings, tg_user.id)
    ):
        await access_screen(event)
        return None
    return user


def username(event: CallbackQuery | Message) -> str:
    tg_user = event.from_user
    if tg_user is None:
        return "unknown"
    return tg_user.username or tg_user.full_name
