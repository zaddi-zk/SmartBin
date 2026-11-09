from __future__ import annotations

from typing import Any

import ui
from handlers.common import (
    consume_quota,
    free_watermark,
    is_admin,
    mark_first,
    quota_decision,
)
from models import Card, utc_date
from render import card_block, verdict_block, verdict_header


async def gate_view(runtime: Any, gate_name: str) -> tuple[str, dict]:
    """Public label + health for a gate. Identity stays private."""
    gates = await runtime.db.list_gates()
    for index, gate in enumerate(gates, start=1):
        if str(gate["name"]) == str(gate_name):
            return f"gate-{index}", runtime.canary.stats(gate)
    return "gate-1", {}


async def ensure_user(runtime: Any, user: Any):
    await runtime.db.ensure_user(
        user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )
    return await runtime.db.reset_quota_if_needed(user.id)


async def lookup(runtime: Any, arg: str):
    if len(arg) == 6:
        return await runtime.store.get_prefix(arg)
    return await runtime.store.get(arg)


async def _paywall(message: Any) -> None:
    await message.answer(
        ui.paywall_text(), parse_mode="HTML", reply_markup=ui.paywall_kb()
    )


async def _bin_result(
    message: Any, runtime: Any, arg: str, user: Any, db_user: Any
) -> None:
    record = await lookup(runtime, arg)
    await mark_first(runtime.db, "first_bin")
    await runtime.db.stats_bump(f"day:{utc_date()}:bins")
    await message.answer(
        card_block(record, free=free_watermark(db_user, runtime.config)),
        parse_mode="HTML",
        reply_markup=ui.bin_kb(arg),
    )


async def perform_bin_lookup(
    message: Any, runtime: Any, arg: str, user: Any = None
) -> None:
    user = user or message.from_user
    db_user = await ensure_user(runtime, user)
    decision = quota_decision(db_user, "bin", runtime.config)
    if not decision["allowed"]:
        await _paywall(message)
        return

    await consume_quota(runtime.db, user.id, "bin", decision["consume"])
    await _bin_result(message, runtime, arg, user, db_user)


async def send_cached_bin(
    message: Any, runtime: Any, arg: str, user: Any = None
) -> None:
    """Render a BIN reached from a result. Counts as a BIN lookup, never free."""
    user = user or message.from_user
    db_user = await ensure_user(runtime, user)
    decision = quota_decision(db_user, "bin", runtime.config)
    if not decision["allowed"]:
        await _paywall(message)
        return

    await consume_quota(runtime.db, user.id, "bin", decision["consume"])
    await _bin_result(message, runtime, arg, user, db_user)


async def run_check_and_render(
    message: Any,
    runtime: Any,
    raw: str,
    card: Card | None = None,
    user: Any = None,
) -> None:
    try:
        parsed = card or Card.parse(raw)
    except ValueError as exc:
        await message.answer(
            ui.check_invalid(str(exc)), parse_mode="HTML", reply_markup=ui.cancel_kb()
        )
        return

    user = user or message.from_user
    db_user = await ensure_user(runtime, user)
    decision = quota_decision(db_user, "check", runtime.config)
    if not decision["allowed"]:
        await _paywall(message)
        return

    await consume_quota(runtime.db, user.id, "check", decision["consume"])
    outcome = await runtime.runner.run(user.id, raw)
    if outcome.verdict == "3DS-CHALLENGE":
        # Integer storage cannot express half a check: a challenge read refunds it.
        if decision["consume"] == "free":
            await runtime.db.execute(
                "UPDATE users SET free_check=CASE WHEN free_check>0 THEN free_check-1 ELSE 0 END "
                "WHERE user_id=?",
                (user.id,),
            )
        elif decision["consume"] == "credit":
            await runtime.db.execute(
                "UPDATE users SET credits=credits+1 WHERE user_id=?",
                (user.id,),
            )
    await mark_first(runtime.db, "first_check")
    gate_label, health = await gate_view(runtime, outcome.gate)
    await message.answer(
        verdict_header(outcome, health, gate_label)
        + "\n"
        + verdict_block(
            outcome, parsed, free=free_watermark(db_user, runtime.config), gate_label=gate_label
        ),
        parse_mode="HTML",
        reply_markup=ui.verdict_kb(parsed.bin8),
    )


async def send_menu(message: Any, runtime: Any, user: Any = None) -> None:
    """Send main menu with instant response - single message with inline keyboard."""
    user = user or message.from_user
    db_user = await ensure_user(runtime, user)
    admin = is_admin(user.id, runtime.config)
    # Delete old message for instant response
    try:
        await message.delete()
    except:
        pass
    await message.answer(
        ui.menu_text(runtime.config, db_user),
        parse_mode="HTML",
        reply_markup=ui.dashboard_kb(admin, runtime.config.miniapp_base),
    )
