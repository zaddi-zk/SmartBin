from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
)

import ui
from payments.stars import (
    PRODUCTS,
    create_invoice_link,
    handle_successful_payment,
    price_for,
)

log = logging.getLogger("smartbin.pay")
router = Router(name="pay")


async def send_store(message: Message, runtime, user: Any = None) -> None:
    user = user or message.from_user
    await runtime.db.ensure_user(
        user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )
    await message.answer(
        ui.premium_text(runtime.config),
        parse_mode="HTML",
        reply_markup=ui.premium_kb(runtime.config),
    )


@router.message(Command("premium"))
async def premium_cmd(message: Message, runtime) -> None:
    await send_store(message, runtime)


@router.callback_query(F.data.startswith("buy:"))
async def buy_callback(callback: CallbackQuery, runtime) -> None:
    product = (callback.data or "").split(":", 1)[1]
    if product not in PRODUCTS:
        await callback.answer("Unknown product", show_alert=True)
        return
    try:
        link = await create_invoice_link(runtime.bot, runtime.config, product)
    except Exception as exc:
        log.warning("event=invoice_failed product=%s error=%s", product, exc)
        await callback.answer("Payments are not configured yet.", show_alert=True)
        return
    amount = price_for(runtime.config, product)
    await callback.message.answer(
        f"{PRODUCTS[product]['title']}\n{amount} XTR\n\nTap to pay:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"Pay {amount} XTR", url=link)]]
        ),
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, runtime) -> None:
    payment = message.successful_payment
    if payment is None:
        return
    result = await handle_successful_payment(
        runtime.db, runtime.config, payment, message.from_user.id
    )
    if result.get("duplicate"):
        await message.answer("Payment already applied.")
        return
    if result.get("granted"):
        await message.answer("Payment received. Premium/credits applied.")
    else:
        await message.answer("Payment received, but entitlement failed. Contact admin.")
