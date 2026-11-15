from __future__ import annotations

import json
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import ui
from core.canaries import percentile
from handlers.common import claim_usage, parse_bin_arg
from models import utc_date
from render import DISPLAY_VERSION, FOOTER_PREFIX, status_block, status_header
from states import Flow

router = Router(name="status")

ALLOWED_FIELDS = {
    "brand", "level", "bank", "phone", "site", "country", "iso2",
    "currency", "prepaid", "commercial", "card_length", "luhn",
    "avs", "dob", "ssn", "mmn",
}


def _json(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


async def _stats(runtime) -> dict:
    db = runtime.db
    all_stats = await db.stats_all()
    latencies = [
        float(r["latency_ms"])
        for r in await db.fetchall(
            "SELECT latency_ms FROM checks WHERE latency_ms>0 ORDER BY id DESC LIMIT 200"
        )
    ]
    gates = []
    for index, gate in enumerate(await db.list_gates(), start=1):
        info = runtime.canary.stats(gate)
        info["label"] = f"gate-{index}"
        gates.append(info)
    revenue = int(
        await db.scalar(
            "SELECT COALESCE(SUM(amount_xtr),0) FROM receipts WHERE state='paid'", (), 0
        )
    )
    checks_today = int(all_stats.get(f"day:{utc_date()}:checks", 0))
    return {
        "uptime": runtime.uptime(),
        "checks_today": checks_today,
        "cache_depth": runtime.store.depth(),
        "p50": int(percentile(latencies, 50)),
        "p95": int(percentile(latencies, 95)),
        "start": int(all_stats.get("start", 0)),
        "first_bin": int(all_stats.get("first_bin", 0)),
        "first_check": int(all_stats.get("first_check", 0)),
        "first_payment": int(all_stats.get("first_payment", 0)),
        "revenue_xtr": revenue,
        "gates": gates,
    }


async def send_status(message: Message, runtime, user: Any = None) -> None:
    user = user or message.from_user
    await runtime.db.ensure_user(
        user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )
    stats = await _stats(runtime)
    await message.answer(
        status_header(stats) + "\n" + status_block(stats),
        parse_mode="HTML",
        reply_markup=ui.status_kb(),
    )


@router.message(Command("status"))
async def status_cmd(message: Message, runtime) -> None:
    await send_status(message, runtime)


@router.message(Command("gates"))
async def gates_cmd(message: Message, runtime) -> None:
    lines = ["GATE HEALTH (identity private)"]
    for index, gate in enumerate(await runtime.db.list_gates(), start=1):
        info = runtime.canary.stats(gate)
        lines.append(
            "gate-{i}  {state:<7} pass {rate:>5}%  p50 {p50:>5}ms  p95 {p95:>5}ms  last {last}".format(
                i=index,
                state="HEALTHY" if info.get("enabled") else "DEGRADED",
                rate=info.get("pass_rate", 0),
                p50=info.get("p50", 0),
                p95=info.get("p95", 0),
                last=info.get("last_canary_utc", "") or "never",
            )
        )
    lines.append("ROUTING      auto-route · ISO2 first")
    lines.append(f"{FOOTER_PREFIX} v{DISPLAY_VERSION} · cache · {utc_date()} · @SmartBlN_Bot")
    await message.answer(
        "<pre>" + "\n".join(lines) + "</pre>",
        parse_mode="HTML",
        reply_markup=ui.menu_only_kb(),
    )


async def send_why(message: Message, runtime, bin_arg: str, user: Any = None) -> None:
    user = user or message.from_user
    await runtime.db.ensure_user(
        user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )
    db_user = await runtime.db.reset_quota_if_needed(user.id)
    record = (
        await runtime.store.get(bin_arg)
        if len(bin_arg) == 8
        else await runtime.store.get_prefix(bin_arg)
    )
    if record is None:
        await message.answer(
            f"❌ No data cached for BIN <code>{bin_arg}</code>",
            parse_mode="HTML",
            reply_markup=ui.menu_only_kb(),
        )
        return
    # Full provenance + 2FA history is BIN intelligence: one daily lookup.
    decision = await claim_usage(runtime.db, db_user, "bin", runtime.config)
    if not decision["allowed"]:
        await message.answer(
            ui.paywall_text(), parse_mode="HTML", reply_markup=ui.paywall_kb()
        )
        return
    sources = _json(record.sources_json)
    confs = _json(record.field_conf_json)
    lines = [
        f"BIN {record.bin}  {record.brand}  {record.level}",
        f"SOURCES      {', '.join(sources.keys()) or 'none'}",
        f"VERIFIED     {record.verified_utc or 'UNKNOWN'}",
        f"SAMPLE       {record.sample_size}",
    ]
    for field, provenance in sources.items():
        conf = float(confs.get(field, 0.0) or 0.0)
        pairs = ", ".join(f"{name}={value}" for name, value in provenance.items())
        lines.append(f"FIELD        {field} {int(round(conf))}%  {pairs}")
    last = None
    for row in await runtime.db.checks_for_bin(record.bin, 5):
        if row["acs_host"]:
            last = row
            break
    if last is not None:
        lines.append(
            f"LAST ACS     {last['acs_host']}  gate={last['gate']}  at={last['created_utc']}"
        )
    lines.append(
        f"2FA          {record.twofa}  conf {int(round(record.twofa_conf))}  "
        f"reads {record.twofa_reads}  last {record.last_twofa_utc or 'never'}"
    )
    lines.append(f"{FOOTER_PREFIX} v{DISPLAY_VERSION} · cache · {utc_date()} · @SmartBlN_Bot")
    await message.answer(
        "<pre>" + "\n".join(lines) + "</pre>",
        parse_mode="HTML",
        reply_markup=ui.bin_kb(bin_arg),
    )


@router.message(Command("why"))
async def why_cmd(message: Message, command: CommandObject, runtime) -> None:
    arg = parse_bin_arg(command.args or "")
    if arg is None:
        await message.answer(
            "Usage: /why <6 or 8 digit BIN>", reply_markup=ui.menu_only_kb()
        )
        return
    await send_why(message, runtime, arg)


async def queue_correction(message: Message, runtime, bin_arg: str, field: str, value: str) -> None:
    await runtime.db.ensure_user(
        message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )
    await runtime.db.add_correction(message.from_user.id, bin_arg, field, value)
    await message.answer(
        ui.correction_done_text(bin_arg, field, value),
        parse_mode="HTML",
        reply_markup=ui.correction_done_kb(bin_arg),
    )


@router.message(Command("correct"))
async def correct_cmd(
    message: Message, command: CommandObject, state: FSMContext, runtime
) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await state.set_state(Flow.correct_bin)
        await message.answer(
            ui.correct_bin_prompt(), parse_mode="HTML", reply_markup=ui.cancel_kb()
        )
        return
    parts = raw.split(maxsplit=2)
    if len(parts) != 3:
        await message.answer(
            "Usage: /correct <bin> <field> <value>", reply_markup=ui.cancel_kb()
        )
        return
    arg, field, value = parts
    bin_arg = parse_bin_arg(arg)
    if bin_arg is None:
        await message.answer("BIN must be 6 or 8 digits.", reply_markup=ui.cancel_kb())
        return
    field = field.strip().lower()
    if field not in ALLOWED_FIELDS:
        await message.answer(
            f"Unknown field. Allowed: {', '.join(sorted(ALLOWED_FIELDS))}",
            reply_markup=ui.cancel_kb(),
        )
        return
    await queue_correction(message, runtime, bin_arg, field, value.strip())
