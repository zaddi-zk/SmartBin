"""Keyboards (section 19). callback_data stays well under 64 bytes."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup as KB

from app.exec.check import CheckOutcome
from app.routing.router import GateView


def _kb(*rows: list[B]) -> KB:
    return KB(inline_keyboard=list(rows))


make_kb = _kb
Button = B


def back(data: str = "hub:main", label: str = "⬅️ Back") -> B:
    return B(text=label, callback_data=data)


def back_kb(data: str = "hub:main", label: str = "⬅️ Back") -> KB:
    """A one-button keyboard: every node has a return path (section 10.1)."""
    return _kb([back(data, label)])


def main_menu() -> KB:
    return _kb(
        [B(text="💳 Check Card", callback_data="hub:hub")],
        [B(text="🔍 BIN", callback_data="bin:new"), B(text="📁 Mass", callback_data="hub:mass")],
        [
            B(text="🔌 Proxies", callback_data="px:set"),
            B(text="📊 Status", callback_data="status:all"),
        ],
        [
            B(text="👤 Profile", callback_data="me"),
            B(text="💠 Plans", callback_data="plans"),
            B(text="🎁 Refer", callback_data="ref"),
        ],
    )


def gate_hub() -> KB:
    return _kb(
        [
            B(text="🛡️ AUTH", callback_data="hub:auth"),
            B(text="💰 CHARGE", callback_data="hub:charge"),
        ],
        [
            B(text="🎯 HITTERS", callback_data="hub:hit"),
            B(text="🔪 KILL", callback_data="hub:kill"),
        ],
        [B(text="🧠 AUTO PICK", callback_data="hub:auto"), back()],
    )


def lane_gates(lane: str, gates: list[GateView]) -> KB:
    rows = [
        [
            B(
                text=f"{_gate_dot(g)} {g.label}",
                callback_data=f"gate:info:{g.id}",
            )
        ]
        for g in gates
    ]
    rows.append([back("hub:hub", "⬅️ Back")])
    return _kb(*rows)


def _gate_dot(gate: GateView) -> str:
    return "⚪" if not gate.enabled else _dot(gate.state)


def _dot(state: str) -> str:
    return {"HEALTHY": "🟢", "DEGRADED": "🟡", "HALF_OPEN": "🟡", "OPEN": "🔴"}.get(state, "⚪")


def gate_detail(gate: GateView) -> KB:
    rows: list[list[B]] = []
    if gate.enabled:
        rows.append([B(text="▶️ Use this gate", callback_data=f"run:{gate.id}")])
    rows.append(
        [
            B(text="📈 Stats", callback_data=f"gate:act:{gate.id}:stats"),
            B(text="📁 Mass", callback_data=f"gate:act:{gate.id}:mass"),
        ]
    )
    if not gate.enabled or gate.state == "OPEN":
        rows.append([B(text="🔀 Nearest healthy", callback_data=f"gate:near:{gate.id}")])
    rows.append([back(f"hub:{gate.lane}", "⬅️ Back")])
    return _kb(*rows)


def armed(gate: GateView) -> KB:
    return _kb(
        [B(text="📋 Paste hint", callback_data="card:hint")],
        [B(text="🔁 Change gate", callback_data="hub:hub"), back("hub:main", "⬅️ Menu")],
    )


def result(outcome: CheckOutcome, sibling_id: str | None) -> KB:
    rows: list[list[B]] = []
    if outcome.verdict.value in ("APPROVED", "LIVE", "LIVE_3DS"):
        rows.append([B(text="📋 Copy", callback_data="card:copy")])
    if outcome.verdict.value in ("RETRY", "INCONCLUSIVE", "GATE_FAULT"):
        rows.append([B(text="🔁 Retry", callback_data="card:retry")])
    if outcome.verdict.value == "DEAD" and sibling_id:
        rows.append([B(text="🔁 Retry on sibling", callback_data=f"card:sibling:{sibling_id}")])
    if outcome.verdict.value in ("DEAD", "GATE_FAULT"):
        rows.append([B(text="➡️ Next gate", callback_data="card:next")])
    rows.append([B(text="🔪 Kill", callback_data="card:kill"), back("card:rearm", "⬅️ Re-arm")])
    return _kb(*rows)


def kill_result() -> KB:
    return _kb(
        [B(text="🔁 Rerun", callback_data="card:kill"), B(text="⬅️ Menu", callback_data="hub:main")],
    )


def bin_result(best_gate: str | None) -> KB:
    rows: list[list[B]] = []
    if best_gate:
        rows.append([B(text="🛡️ Run on best gate", callback_data=f"bin:run:{best_gate}")])
    rows.append([B(text="🔍 New BIN", callback_data="bin:new"), back()])
    return _kb(*rows)


def status_kb() -> KB:
    return _kb(
        [B(text="🔄 Refresh", callback_data="status:all"), back()],
    )


def proxy_kb() -> KB:
    return _kb(
        [
            B(text="🎁 10 free / 24h", callback_data="px:free"),
            B(text="➕ Set", callback_data="px:add"),
        ],
        [B(text="🧪 Test", callback_data="px:test"), B(text="🗑 Remove", callback_data="px:clear")],
        [back()],
    )


def profile_kb() -> KB:
    return _kb(
        [
            B(text="📈 My stats", callback_data="me:stats"),
            B(text="💠 Plans", callback_data="plans"),
        ],
        [back()],
    )


def plans_kb() -> KB:
    return _kb(
        [
            B(text="⚡ Buy credits", callback_data="plans:buy"),
            B(text="🎟 Redeem", callback_data="plans:key"),
        ],
        [back()],
    )


def admin_kb() -> KB:
    return _kb(
        [
            B(text="🏷️ Gates", callback_data="adm:gates"),
            B(text="📈 Stats", callback_data="adm:stats"),
        ],
        [B(text="👥 Users", callback_data="adm:u"), B(text="🧾 Log", callback_data="adm:log")],
        [back()],
    )


def admin_gates(gates: list[GateView]) -> KB:
    rows = [
        [B(text=f"{'🟢' if g.enabled else '⚪'} {g.label}", callback_data=f"adm:g:{g.id}")]
        for g in gates
    ]
    rows.append([back("adm", "⬅️ Back")])
    return _kb(*rows)


def admin_gate(gate: GateView) -> KB:
    return _kb(
        [
            B(text="🔬 Force HALF_OPEN", callback_data=f"adm:g:{gate.id}:probe"),
            B(text="🔁 Toggle", callback_data=f"adm:g:{gate.id}:toggle"),
        ],
        [back("adm:gates", "⬅️ Back")],
    )
