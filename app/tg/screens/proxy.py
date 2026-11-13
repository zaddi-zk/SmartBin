"""Proxy screen (section 13)."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.db.models import Proxy
from app.runtime import runtime
from app.tg import keyboards as kb
from app.tg import render
from app.tg.guards import load_user
from app.tg.states import Flow

router = Router(name="proxy")


def parse_proxy(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    if "://" in text:
        return text
    parts = text.split(":")
    if len(parts) == 2 and all(parts):
        return f"http://{parts[0]}:{parts[1]}"
    if len(parts) == 4 and all(parts):
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    return None


async def proxy_view(session: AsyncSession, user_id: int) -> str:
    user = await repo.get_user(session, user_id)
    if user is None or user.proxy_id is None:
        return "🔌 <b>PROXIES</b>\n\nNow: <b>direct egress</b>"
    proxy = await session.get(Proxy, user.proxy_id)
    if proxy is None:
        return "🔌 <b>PROXIES</b>\n\nNow: <b>direct egress</b>"
    status = "🟢" if proxy.alive else "🔴"
    return (
        "🔌 <b>PROXIES</b>\n\n"
        f"Now: <code>{render.esc(proxy.url)}</code> · {render.flag(proxy.country)} · "
        f"{status} {proxy.latency_ms or '?'}ms"
    )


@router.message(Command("px"))
async def px_command(message: Message, session: AsyncSession, bot: Bot, settings: Settings) -> None:
    user = await load_user(session, message)
    if user is None:
        return
    await message.answer(await proxy_view(session, user.id), reply_markup=kb.proxy_kb())


@router.callback_query(F.data == "px:set")
async def px_set(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, callback)
    if user is None or callback.message is None:
        return
    await callback.message.edit_text(await proxy_view(session, user.id), reply_markup=kb.proxy_kb())


@router.callback_query(F.data == "px:free")
async def px_free(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, callback)
    if user is None or callback.message is None:
        return
    batch = await runtime().proxies.free_batch(session, user)
    if not batch:
        await callback.message.answer(
            "🎁 <b>FREE BATCH</b>\n"
            "<blockquote>One batch per 24h, or the pool is empty.</blockquote>"
        )
        return
    lines = [f"🎁 <b>{len(batch)} FREE PROXIES</b>", ""]
    lines.extend(
        f"<code>{render.esc(proxy.url)}</code> · {render.flag(proxy.country)} · "
        f"{proxy.latency_ms or '?'}ms"
        for proxy in batch
    )
    await callback.message.answer("\n".join(lines))


@router.callback_query(F.data == "px:add")
async def px_add(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(Flow.await_proxy)
    await callback.message.edit_text(
        "➕ <b>SET PROXY</b>\n\nSend <code>host:port</code> or <code>host:port:user:pass</code>.",
        reply_markup=kb.back_kb("px:set", "⬅️ Back"),
    )


@router.message(Flow.await_proxy, F.text)
async def px_text(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if message.text is None:
        return
    url = parse_proxy(message.text)
    if url is None:
        await message.answer("⚠️ Could not parse that. Use <code>host:port:user:pass</code>.")
        return
    check = await runtime().proxies.test(url)
    if not check.alive:
        await message.answer("🔴 Proxy did not answer api.ipify.org.")
        return
    proxy = await repo.add_proxy(session, url)
    proxy.alive = True
    proxy.latency_ms = check.latency_ms
    user = await repo.ensure_user(session, message.from_user.id if message.from_user else 0)
    user.proxy_id = proxy.id
    await state.clear()
    await message.answer(
        f"🟢 Bound <code>{render.esc(url)}</code> · {check.latency_ms}ms",
        reply_markup=kb.proxy_kb(),
    )


@router.callback_query(F.data == "px:test")
async def px_test(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, callback)
    if user is None or callback.message is None or user.proxy_id is None:
        if callback.message is not None:
            await callback.message.answer("No proxy set.")
        return
    proxy = await session.get(Proxy, user.proxy_id)
    if proxy is None:
        await callback.message.answer("No proxy set.")
        return
    check = await runtime().proxies.test(proxy.url)
    proxy.alive = check.alive
    proxy.latency_ms = check.latency_ms
    await callback.message.answer(
        f"{'🟢' if check.alive else '🔴'} <code>{render.esc(proxy.url)}</code> · "
        f"{check.latency_ms}ms · {render.esc(check.ip or '—')}"
    )


@router.callback_query(F.data == "px:clear")
async def px_clear(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, callback)
    if user is None or callback.message is None:
        return
    user.proxy_id = None
    await callback.message.edit_text(await proxy_view(session, user.id), reply_markup=kb.proxy_kb())
