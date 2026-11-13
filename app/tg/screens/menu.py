"""Menu, hub, profile, plans and referral screens."""

from __future__ import annotations

from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.db.models import Proxy, User
from app.routing.router import build_view, route
from app.tg import keyboards as kb
from app.tg import render
from app.tg.guards import load_user, username
from app.tg.states import Flow

router = Router(name="menu")


async def menu_view(
    session: AsyncSession, user: User, display_name: str
) -> tuple[str, InlineKeyboardMarkup]:
    gates = await repo.list_gates(session)
    states = await repo.gate_states(session)
    views = [build_view(gate, states.get(gate.id)) for gate in gates]
    p50 = min(
        (view.p50_ms for view in views if view.p50_ms and view.state != "OPEN"),
        default=None,
    )
    sticky_label = "Auto"
    if user.sticky_gate:
        sticky = await repo.get_gate(session, user.sticky_gate)
        sticky_label = sticky.label if sticky is not None else user.sticky_gate
    proxy_label = "none · direct"
    if user.proxy_id is not None:
        proxy = await session.get(Proxy, user.proxy_id)
        if proxy is not None:
            proxy_label = f"{render.flag(proxy.country)} · {proxy.latency_ms or '?'}ms"
    text = render.menu_text(
        username=display_name,
        credits=Decimal(user.credits),
        tier=user.tier,
        proxy_label=proxy_label,
        sticky_label=sticky_label,
        p50_ms=p50,
    )
    return text, kb.main_menu()


@router.message(CommandStart())
async def start(message: Message, session: AsyncSession, bot: Bot, settings: Settings) -> None:
    user = await load_user(session, message, bot=bot, settings=settings)
    if user is None:
        return
    text, markup = await menu_view(session, user, username(message))
    await message.answer(text, reply_markup=markup)


@router.message(Command("menu"))
async def menu_command(
    message: Message, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    await start(message, session, bot, settings)


@router.callback_query(F.data == "hub:main")
async def main_menu(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, callback, bot=bot, settings=settings)
    if user is None or callback.message is None:
        return
    text, markup = await menu_view(session, user, username(callback))
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "hub:hub")
async def gate_hub(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    if await load_user(session, callback, bot=bot, settings=settings) is None:
        return
    if callback.message is not None:
        await callback.message.edit_text(render.hub_text(), reply_markup=kb.gate_hub())


@router.callback_query(F.data == "hub:auto")
async def auto_pick(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, callback, bot=bot, settings=settings)
    if user is None:
        return
    gates = await repo.list_gates(session)
    states = await repo.gate_states(session)
    views = [build_view(gate, states.get(gate.id)) for gate in gates]
    candidates = route(views, "", "auth")
    if not candidates:
        if callback.message is not None:
            await callback.message.edit_text(
                "🚧 <b>NO HEALTHY GATE</b>\n"
                "<blockquote>Every auth gate is open. 0 cr charged.</blockquote>",
                reply_markup=kb.gate_hub(),
            )
        return
    gate = candidates[0]
    await state.set_state(Flow.await_card)
    await state.update_data(gate_id=gate.id, lane="auth")
    if callback.message is not None:
        await callback.message.edit_text(render.armed_text(gate), reply_markup=kb.armed(gate))


@router.callback_query(F.data == "me")
async def profile(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, callback, bot=bot, settings=settings)
    if user is None or callback.message is None:
        return
    counts = await repo.check_counts(session, user.id)
    await callback.message.edit_text(
        render.profile_text(
            username=username(callback),
            user_id=user.id,
            credits=Decimal(user.credits),
            tier=user.tier,
            checks=sum(counts.values()),
            sticky=user.sticky_gate or "",
        ),
        reply_markup=kb.profile_kb(),
    )


@router.callback_query(F.data == "me:stats")
async def my_stats(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, callback, bot=bot, settings=settings)
    if user is None or callback.message is None:
        return
    counts = await repo.check_counts(session, user.id)
    if not counts:
        body = "No checks yet."
    else:
        body = "\n".join(
            f"{render.esc(name)}: <b>{count}</b>" for name, count in sorted(counts.items())
        )
    await callback.message.edit_text(f"📈 <b>MY STATS</b>\n\n{body}", reply_markup=kb.profile_kb())


@router.callback_query(F.data == "me:verify")
async def verify(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    from app.tg.guards import member_of_all

    if callback.from_user is None or not await member_of_all(bot, settings, callback.from_user.id):
        if callback.message is not None:
            await callback.message.answer("🔒 Join both channels first, then press Verify.")
        return
    user = await repo.ensure_user(session, callback.from_user.id)
    user.joined_ok = True
    if callback.message is not None:
        text, markup = await menu_view(session, user, username(callback))
        await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "plans")
async def plans(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    if await load_user(session, callback, bot=bot, settings=settings) is None:
        return
    if callback.message is not None:
        await callback.message.edit_text(
            "💠 <b>PLANS</b>\n\n"
            "Credits are debited only on a terminal verdict.\n"
            "<blockquote>Stars checkout is not wired in this build. "
            "An admin can grant credits with /grant.</blockquote>",
            reply_markup=kb.plans_kb(),
        )


@router.callback_query(F.data == "plans:buy")
async def buy(callback: CallbackQuery) -> None:
    if callback.message is not None:
        await callback.message.answer("💠 Stars checkout is not configured in this build.")


@router.callback_query(F.data == "plans:key")
async def redeem(callback: CallbackQuery) -> None:
    if callback.message is not None:
        await callback.message.answer("🎟 No redeemable keys yet.")


@router.callback_query(F.data == "ref")
async def referral(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    user = await load_user(session, callback, bot=bot, settings=settings)
    if user is None or callback.message is None:
        return
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{user.id}"
    await callback.message.edit_text(
        f"🎁 <b>REFERRAL</b>\n\n🔗 <code>{render.esc(link)}</code>\n"
        "<blockquote>+10 cr when your referee completes their 5th check.</blockquote>",
        reply_markup=kb.profile_kb(),
    )
