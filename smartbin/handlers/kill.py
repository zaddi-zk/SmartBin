from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import ui
from models import Card
from states import Flow

log = logging.getLogger("smartbin.kill")

router = Router(name="kill")


@router.message(Command("kill"))
async def kill_cmd(message: Message, state: FSMContext, runtime) -> None:
    """Start kill card flow."""
    await state.set_state(Flow.kill)
    await message.answer(
        ui.kill_prompt(), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )


@router.message(Flow.kill)
async def kill_input(message: Message, state: FSMContext, runtime) -> None:
    """Parse card for killing."""
    raw = message.text.strip()
    
    try:
        card = Card.parse(raw)
    except ValueError as exc:
        await message.answer(
            ui.kill_invalid(str(exc)), parse_mode="HTML", reply_markup=ui.cancel_kb()
        )
        return
    
    # Check user credits
    db_user = await runtime.db.get_user(message.from_user.id)
    if not db_user:
        db_user = await runtime.db.ensure_user(
            message.from_user.id,
            username=message.from_user.username or "",
            first_name=message.from_user.first_name or "",
        )
    
    # Each kill attempt costs credits
    attempts_cost = runtime.config.kill_attempts_cost
    total_cost = attempts_cost * runtime.config.kill_max_attempts
    
    if int(db_user["credits"]) < attempts_cost:
        await message.answer(
            ui.kill_insufficient_credits(attempts_cost),
            parse_mode="HTML",
            reply_markup=ui.paywall_kb(),
        )
        return
    
    await state.set_state(Flow.kill_confirm)
    await state.update_data(
        kill_raw=raw,
        kill_card=card,
        kill_attempts=runtime.config.kill_max_attempts,
        kill_cost_per_attempt=attempts_cost,
    )
    await message.answer(
        ui.confirm_kill_text(card, runtime.config),
        parse_mode="HTML",
        reply_markup=ui.confirm_kill_kb(),
    )


@router.callback_query(lambda c: c.data == "flow:runkill")
async def run_kill(callback: Message, state: FSMContext, runtime) -> None:
    """Execute kill operation."""
    data = await state.get_data()
    raw = data.get("kill_raw")
    card = data.get("kill_card")
    max_attempts = data.get("kill_attempts", 5)
    cost_per_attempt = data.get("kill_cost_per_attempt", 1)
    
    await callback.answer()
    
    # Start kill operation
    results = []
    declined = 0
    approved = 0
    total_latency = 0
    
    for attempt in range(max_attempts):
        outcome = await runtime.runner.run(callback.from_user.id, raw, purpose="kill")
        latency = outcome.latency_ms or 0
        total_latency += latency
        
        if outcome.live:
            approved += 1
        else:
            declined += 1
        
        results.append({
            "attempt": attempt + 1,
            "verdict": outcome.verdict,
            "live": outcome.live,
            "latency_ms": latency,
        })
        
        # Small delay between attempts to avoid rate limiting
        if attempt < max_attempts - 1:
            await asyncio.sleep(0.5)
    
    # Deduct credits
    total_cost = cost_per_attempt * len(results)
    await runtime.db.user_spend(callback.from_user.id, total_cost)
    
    # Calculate average latency
    avg_latency = total_latency / len(results) if results else 0
    
    # Send kill result
    await callback.message.answer(
        ui.kill_result_text(
            card, results, declined, approved, avg_latency, runtime.config
        ),
        parse_mode="HTML",
        reply_markup=ui.kill_result_kb(card.bin8),
    )
    
    await state.clear()


@router.callback_query(lambda c: c.data == "flow:kill")
async def kill_flow_start(callback: Message, state: FSMContext) -> None:
    """Start kill flow from menu."""
    await callback.answer()
    await state.set_state(Flow.kill)
    await callback.message.answer(
        ui.kill_prompt(), parse_mode="HTML", reply_markup=ui.cancel_kb()
    )
