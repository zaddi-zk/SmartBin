from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from handlers.common import is_admin
from handlers.status import ALLOWED_FIELDS
from models import utc_date, utcnow_iso
from payments.stars import refund
from util_net import timed_get

log = logging.getLogger("smartbin.admin")
router = Router(name="admin")


class AdminStates(StatesGroup):
    proxy_add = State()
    credits = State()
    broadcast = State()
    refund = State()


def _guard(user_id: int, config) -> bool:
    return is_admin(user_id, config)


def home_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Proxy Pool", callback_data="adm:proxies"),
             InlineKeyboardButton(text="🛡️ Gates", callback_data="adm:gates")],
            [InlineKeyboardButton(text="Corrections", callback_data="adm:corr"),
             InlineKeyboardButton(text="Credits", callback_data="adm:credits")],
            [InlineKeyboardButton(text="Broadcast", callback_data="adm:broadcast"),
             InlineKeyboardButton(text="Refund", callback_data="adm:refund")],
            [InlineKeyboardButton(text="🏠 Menu", callback_data="flow:menu")],
        ]
    )


def gate_label(name: str) -> str:
    """Display names only. Never leak internal key/index wording."""
    clean = (name or "gate").replace("_", " ").strip().title()
    if clean.lower().startswith("stripe"):
        return "Stripe Auth"
    return clean


def _ago(raw: str) -> str:
    """Relative heartbeat for the gate monitor. Blank or bad timestamps read 'never'."""
    if not raw:
        return "never"
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return "never"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    seconds = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m ago"


async def dashboard(runtime) -> str:
    db = runtime.db
    stats = await db.stats_all()
    verdicts = await db.verdict_distribution()
    revenue = int(await db.scalar("SELECT COALESCE(SUM(amount_xtr),0) FROM receipts WHERE state='paid'", (), 0))
    premium = int(await db.scalar("SELECT COUNT(*) FROM users WHERE premium_until > ?", (utcnow_iso(),), 0))
    dist = ", ".join(f"{k}:{v}" for k, v in verdicts.items()) or "none"
    pending = await db.count("corrections", "status='pending'")
    key_rows = runtime.gates.stripe_keys.snapshot()
    key_ready = sum(1 for row in key_rows if row["ready"])
    key_line = (
        f"{key_ready}/{len(key_rows)} ready"
        if key_rows
        else "0 (test mode / unset)"
    )
    return (
        "ADMIN DASHBOARD\n\n"
        f"users          {await db.count('users')}   monthly {await db.monthly_users()}\n"
        f"premium        {premium}\n"
        f"stripe keys    {key_line}\n"
        f"checks today   {int(stats.get('day:' + utc_date() + ':checks', 0))}\n"
        f"revenue        {revenue} XTR\n"
        f"proxies        {await db.count('proxies')}\n"
        f"corrections    {pending}\n"
        f"funnel         start {int(stats.get('start', 0))} -> bin {int(stats.get('first_bin', 0))} -> check {int(stats.get('first_check', 0))} -> pay {int(stats.get('first_payment', 0))}\n"
        f"verdicts       {dist}"
    )


@router.message(Command("admin"))
async def admin_cmd(message: Message, command: CommandObject, runtime) -> None:
    if not _guard(message.from_user.id, runtime.config):
        await message.answer("Admin only")
        return
    raw = (command.args or "").strip()
    if raw.lower().startswith("broadcast "):
        text = raw[len("broadcast "):].strip()
        count = await _broadcast(runtime, text)
        await message.answer(f"Broadcast sent to {count} users.")
        return
    await message.answer(await dashboard(runtime), reply_markup=home_markup())


async def _broadcast(runtime, text: str) -> int:
    sent = 0
    for row in await runtime.db.fetchall("SELECT user_id FROM users"):
        try:
            await runtime.bot.send_message(int(row["user_id"]), text)
            sent += 1
        except Exception:
            continue
    return sent


@router.callback_query(F.data == "adm:home")
async def cb_home(callback: CallbackQuery, runtime) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    await callback.message.edit_text(await dashboard(runtime), reply_markup=home_markup())
    await callback.answer()


@router.callback_query(F.data == "adm:proxies")
async def cb_proxies(callback: CallbackQuery, runtime, answer: bool = True) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    rows = await runtime.db.list_proxies()
    lines = ["PROXY POOL"]
    buttons = [[InlineKeyboardButton(text="+ Add proxy", callback_data="adm:proxy:add")]]
    for row in rows:
        lines.append(
            f"#{row['id']} {row['scope']:<6} {row['country'] or '--':<2} "
            f"{'ON ' if row['enabled'] else 'OFF'} ok={row['ok_count']} fail={row['fail_count']} {row['last_latency_ms']}ms"
        )
        buttons.append(
            [
                InlineKeyboardButton(text=f"#{row['id']} toggle", callback_data=f"adm:proxy:toggle:{row['id']}"),
                InlineKeyboardButton(text="test", callback_data=f"adm:proxy:test:{row['id']}"),
                InlineKeyboardButton(text="del", callback_data=f"adm:proxy:del:{row['id']}"),
            ]
        )
    buttons.append([InlineKeyboardButton(text="Back", callback_data="adm:home")])
    await callback.message.edit_text(
        "<pre>" + "\n".join(lines) + "</pre>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    if answer:
        await callback.answer()


@router.callback_query(F.data == "adm:proxy:add")
async def cb_proxy_add(callback: CallbackQuery, state: FSMContext, runtime) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    await state.set_state(AdminStates.proxy_add)
    await callback.message.answer("Send: url scope country note\nExample: http://user:pass@host:8080 check DK fast")
    await callback.answer()


@router.message(AdminStates.proxy_add)
async def proxy_add_input(message: Message, state: FSMContext, runtime) -> None:
    if not _guard(message.from_user.id, runtime.config):
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 1:
        await message.answer("Need a proxy URL.")
        return
    url = parts[0]
    scope = parts[1] if len(parts) > 1 else "check"
    country = parts[2] if len(parts) > 2 else ""
    note = parts[3] if len(parts) > 3 else ""
    await runtime.db.add_proxy(url, scope=scope, country=country.upper(), note=note)
    await state.clear()
    await message.answer("Proxy added.", reply_markup=home_markup())


@router.callback_query(F.data.startswith("adm:proxy:"))
async def cb_proxy_action(callback: CallbackQuery, runtime) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 4:
        await callback.answer()
        return
    action, proxy_id = parts[2], int(parts[3])
    row = await runtime.db.fetchone("SELECT * FROM proxies WHERE id=?", (proxy_id,))
    if row is None:
        await callback.answer("Not found", show_alert=True)
        return
    if action == "toggle":
        await runtime.db.update_proxy(proxy_id, enabled=0 if row["enabled"] else 1)
        await callback.answer("Toggled")
    elif action == "del":
        await runtime.db.delete_proxy(proxy_id)
        await callback.answer("Deleted")
    elif action == "test":
        status, latency = await timed_get("https://api.ipify.org?format=json", proxy=str(row["url"]))
        fields = {"last_latency_ms": latency}
        if status:
            fields["ok_count"] = int(row["ok_count"]) + 1
        else:
            fields["fail_count"] = int(row["fail_count"]) + 1
        await runtime.db.update_proxy(proxy_id, **fields)
        await callback.answer(f"HTTP {status} · {latency}ms")
    else:
        await callback.answer()
        return
    await cb_proxies(callback, runtime, answer=False)


@router.callback_query(F.data == "adm:gates")
async def cb_gates(callback: CallbackQuery, runtime, answer: bool = True) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    gates = await runtime.db.list_gates()
    infos = [(row, runtime.canary.stats(row)) for row in gates]
    healthy_count = sum(
        1
        for row, info in infos
        if int(row["enabled"]) and info.get("window_state", -1) == 1
    )
    healthy_all = bool(infos) and healthy_count == len(infos)
    pulse = "🟢" if healthy_all else "🟡"
    lines = [
        f"{pulse} <b>GATE PULSE</b> · <code>{healthy_count}/{len(infos)} healthy</code>"
    ]
    if not infos:
        lines.append("🔴 No gates configured. CORE degrades to BIN-only mode.")
    buttons = []
    for row, info in infos:
        healthy = int(row["enabled"]) and info.get("window_state", -1) == 1
        icon = "🟢" if healthy else "🔴"
        state = "HEALTHY" if healthy else "DEGRADED"
        window = f"{info.get('window_pass', 0)}/{info.get('window_total', 0)}"
        lines.append(
            f"{icon} <b>{gate_label(row['name'])}</b> · {state} · canary <code>{window}</code>"
        )
        lines.append(
            f"<code>p50 {info.get('p50', 0)}ms · p95 {info.get('p95', 0)}ms · "
            f"last {_ago(row['last_canary_utc'])}</code>"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=("🛡️ " if healthy else "⛔ ") + f"{gate_label(row['name'])} · "
                    + ("ON" if int(row["enabled"]) else "OFF"),
                    callback_data=f"adm:gate:toggle:{row['name']}",
                ),
                InlineKeyboardButton(text="🔁 canary now", callback_data=f"adm:gate:canary:{row['name']}"),
                InlineKeyboardButton(text="♻️ reset breaker", callback_data=f"adm:gate:breaker:{row['name']}"),
            ]
        )
    lines.append("<blockquote>Routing: Auto-Route · ISO2 first · canary-gated</blockquote>")
    buttons.append([InlineKeyboardButton(text="Back", callback_data="adm:home")])
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    if answer:
        await callback.answer()


@router.callback_query(F.data.startswith("adm:gate:"))
async def cb_gate_action(callback: CallbackQuery, runtime) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    action, name = parts[2], parts[3]
    row = await runtime.db.get_gate(name)
    if row is None:
        await callback.answer("Not found", show_alert=True)
        return
    if action == "toggle":
        await runtime.db.update_gate(name, enabled=0 if row["enabled"] else 1)
        await callback.answer("Toggled")
    elif action == "canary":
        summary = await runtime.canary.fire_window(name)
        await callback.answer(f"canary {summary.get('window_pass', 0)}/3 {summary.get('action', '')}")
    elif action == "breaker":
        await runtime.db.update_gate(name, breaker_fails=0, breaker_until="", enabled=1)
        await callback.answer("Breaker reset")
    else:
        await callback.answer()
        return
    await cb_gates(callback, runtime, answer=False)


@router.callback_query(F.data == "adm:corr")
async def cb_corrections(callback: CallbackQuery, runtime, answer: bool = True) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    rows = await runtime.db.list_corrections("pending", 10)
    lines = ["CORRECTIONS"]
    buttons = []
    for row in rows:
        lines.append(f"#{row['id']} {row['bin']} {row['field']}={row['value']} by {row['user_id']}")
        buttons.append(
            [
                InlineKeyboardButton(text=f"#{row['id']} approve", callback_data=f"adm:corr:ok:{row['id']}"),
                InlineKeyboardButton(text=f"#{row['id']} reject", callback_data=f"adm:corr:no:{row['id']}"),
            ]
        )
    buttons.append([InlineKeyboardButton(text="Back", callback_data="adm:home")])
    await callback.message.edit_text(
        "<pre>" + "\n".join(lines) + "</pre>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    if answer:
        await callback.answer()


@router.callback_query(F.data.startswith("adm:corr:"))
async def cb_correction_action(callback: CallbackQuery, runtime) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    action, corr_id = parts[2], int(parts[3])
    row = await runtime.db.fetchone("SELECT * FROM corrections WHERE id=?", (corr_id,))
    if row is None:
        await callback.answer("Not found", show_alert=True)
        return
    if action == "no":
        await runtime.db.decide_correction(corr_id, "rejected")
        await callback.answer("Rejected")
        await cb_corrections(callback, runtime, answer=False)
        return
    field = str(row["field"])
    if field not in ALLOWED_FIELDS:
        await callback.answer("Field not allowed", show_alert=True)
        return
    await runtime.db.update_bin_fields(str(row["bin"]), {field: row["value"]})
    await runtime.db.execute(
        "UPDATE bins SET corrections=corrections+1 WHERE bin=?", (str(row["bin"]),)
    )
    reward = runtime.config.correction_reward
    await runtime.db.execute(
        "UPDATE users SET credits=credits+?, corrections=corrections+1 WHERE user_id=?",
        (reward, int(row["user_id"])),
    )
    await runtime.db.decide_correction(corr_id, "applied", reward)
    try:
        await runtime.bot.send_message(
            int(row["user_id"]),
            f"Correction applied for {row['bin']} {field}. +{reward} credits.",
        )
    except Exception:
        pass
    await callback.answer("Applied")
    await cb_corrections(callback, runtime, answer=False)


@router.callback_query(F.data == "adm:credits")
async def cb_credits(callback: CallbackQuery, state: FSMContext, runtime) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    await state.set_state(AdminStates.credits)
    await callback.message.answer("Send: user_id amount")
    await callback.answer()


@router.message(AdminStates.credits)
async def credits_input(message: Message, state: FSMContext, runtime) -> None:
    if not _guard(message.from_user.id, runtime.config):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Need user_id and amount.")
        return
    user_id, amount = int(parts[0]), int(parts[1])
    await runtime.db.ensure_user(user_id)
    await runtime.db.execute(
        "UPDATE users SET credits=credits+? WHERE user_id=?", (amount, user_id)
    )
    await state.clear()
    await message.answer(f"Granted {amount} credits to {user_id}.")


@router.callback_query(F.data == "adm:broadcast")
async def cb_broadcast(callback: CallbackQuery, state: FSMContext, runtime) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    await state.set_state(AdminStates.broadcast)
    await callback.message.answer("Send the broadcast text.")
    await callback.answer()


@router.message(AdminStates.broadcast)
async def broadcast_input(message: Message, state: FSMContext, runtime) -> None:
    if not _guard(message.from_user.id, runtime.config):
        return
    count = await _broadcast(runtime, message.text or "")
    await state.clear()
    await message.answer(f"Broadcast sent to {count} users.")


@router.callback_query(F.data == "adm:refund")
async def cb_refund(callback: CallbackQuery, state: FSMContext, runtime) -> None:
    if not _guard(callback.from_user.id, runtime.config):
        await callback.answer("Admin only", show_alert=True)
        return
    await state.set_state(AdminStates.refund)
    await callback.message.answer("Send: user_id telegram_payment_charge_id")
    await callback.answer()


@router.message(AdminStates.refund)
async def refund_input(message: Message, state: FSMContext, runtime) -> None:
    if not _guard(message.from_user.id, runtime.config):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Need user_id and charge_id.")
        return
    result = await refund(runtime.bot, runtime.db, int(parts[0]), parts[1])
    await state.clear()
    await message.answer(f"Refund: {result}")
