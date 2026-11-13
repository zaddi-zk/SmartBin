"""HTML rendering for every screen. All dynamic text is escaped (section 3.10)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from html import escape

from app.bin.resolver import BinInfo
from app.core.parse import Card, fmt_cr
from app.core.verdicts import Verdict
from app.exec.check import CheckOutcome
from app.exec.kill import KillOutcome
from app.routing.router import GateView


def esc(value: object) -> str:
    return escape(str(value), quote=False)


def flag(iso2: str) -> str:
    code = (iso2 or "").upper()
    if len(code) != 2 or not code.isalpha():
        return "🌍"
    return chr(0x1F1E6 + ord(code[0]) - 65) + chr(0x1F1E6 + ord(code[1]) - 65)


def dot(state: str) -> str:
    return {"HEALTHY": "🟢", "DEGRADED": "🟡", "HALF_OPEN": "🟡", "OPEN": "🔴"}.get(state, "⚪")


def latency(ms: int | None) -> str:
    if not ms:
        return "—"
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"


def seconds_ago(moment: datetime | None) -> str:
    if moment is None:
        return "never"
    delta = int((datetime.now(UTC) - moment).total_seconds())
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    return f"{delta // 3600}h"


def gate_button(gate: GateView) -> str:
    if gate.state == "OPEN":
        return f"🔴 {gate.label} · open"
    p50 = latency(gate.p50_ms)
    success = f" · {gate.success_pct:.0f}%" if gate.success_pct is not None else ""
    return f"{dot(gate.state)} {gate.label} · {fmt_cr(gate.cost_cr)}cr · {p50}{success}"


def menu_text(
    *,
    username: str,
    credits: Decimal,
    tier: str,
    proxy_label: str,
    sticky_label: str,
    p50_ms: int | None,
) -> str:
    return (
        "🧬 <b>VENICE CHECK</b>\n"
        f"<blockquote>⚡ {latency(p50_ms)} p50 · 🛡️ 2-source BIN · "
        "💸 free on failure</blockquote>\n\n"
        f"👤 <code>@{esc(username)}</code> · 💠 <b>{fmt_cr(credits)}</b> cr · "
        f"🎖️ <b>{esc(tier.upper())}</b>\n"
        f"🔌 Proxy: <b>{proxy_label}</b> · 🏁 Gate: <b>{esc(sticky_label)}</b>\n\n"
        "Pick a lane. 🛡️ Auth holds, 💰 Charge takes."
    )


def hub_text() -> str:
    return (
        "🏷️ <b>GATE HUB</b>\n"
        "<blockquote>🛡️ <b>Auth</b> — $0/$1 hold, validates, no money moves\n"
        "💰 <b>Charge</b> — real capture, money moves\n"
        "🎯 <b>Hitters</b> — link & product flows\n"
        "🔪 <b>Kill</b> — escalating ladder, drains the balance</blockquote>\n\n"
        "⚠️ Auth ≠ Charge. Pick the one that matches the job."
    )


LANE_TITLES = {
    "auth": ("🛡️", "AUTH GATES", "hold only, no capture"),
    "charge": ("💰", "CHARGE GATES", "real capture, money moves"),
    "hit": ("🎯", "HITTERS", "link and product flows"),
    "kill": ("🔪", "KILL GATES", "escalating manual-capture ladder"),
}


def gates_text(lane: str, gates: list[GateView]) -> str:
    emoji, title, subtitle = LANE_TITLES.get(lane, ("🏷️", lane.upper(), ""))
    lines = [f"{emoji} <b>{title}</b> · <i>{subtitle}</i>", ""]
    if not gates:
        lines.append("🚧 <i>No gate in this lane yet.</i>")
    for gate in gates:
        if not gate.enabled:
            lines.append(f"⚪ {esc(gate.label)} · <i>adapter pending</i>")
        elif gate.state == "OPEN":
            lines.append(f"🔴 {esc(gate.label)} · <i>open · still tappable</i>")
        else:
            success = f"{gate.success_pct:.1f}%" if gate.success_pct is not None else "—"
            lines.append(
                f"{dot(gate.state)} {esc(gate.label)} · {fmt_cr(gate.cost_cr)} cr · "
                f"<b>{latency(gate.p50_ms)}</b> · {success}"
            )
    return "\n".join(lines)


def detail_text(gate: GateView, keys: int, breaker: str, last_ok: datetime | None) -> str:
    p50 = latency(gate.p50_ms)
    p95 = latency(gate.p95_ms)
    success = f"{gate.success_pct:.1f}%" if gate.success_pct is not None else "—"
    countries = " · ".join(esc(country) for country in gate.countries) or "—"
    three_ds = "handled" if gate.handles_3ds else "not handled"
    avs = "yes" if gate.needs_postal else "no"
    amount = "SetupIntent" if gate.amount_cents == 0 else f"${gate.amount_cents / 100:.2f}"
    return (
        f"🛡️ <b>{esc(gate.label)}</b> {dot(gate.state)} {esc(gate.state)}\n\n"
        f"📊 p50 <b>{p50}</b> · p95 <b>{p95}</b> · ✅ <b>{success}</b> "
        f"({'real samples' if gate.p50_ms else 'no samples'})\n"
        f"🕐 last ok <b>{seconds_ago(last_ok)}</b> · 🔒 breaker <b>{esc(breaker)}</b> · "
        f"🔑 keys <b>{keys}</b>\n"
        f"💰 <b>{fmt_cr(gate.cost_cr)} cr</b> · ⚠️ charged on terminal verdict only\n\n"
        f"🌍 <b>{countries}</b>\n"
        f"🔐 3DS <b>{three_ds}</b> · 📮 AVS <b>{avs}</b> · 💵 <b>{amount}</b>\n\n"
        f"<blockquote>{esc(gate.blurb)}</blockquote>"
    )


def armed_text(gate: GateView) -> str:
    return (
        f"✅ <b>{esc(gate.label)}</b> armed · {fmt_cr(gate.cost_cr)} cr · {dot(gate.state)}\n\n"
        "Paste it.\n"
        "<code>cc|mm|yy|cvv</code> · or <code>cc|mm|yy|cvv|zip</code>\n\n"
        "<i>Also accepts spaces, slashes, colons, semicolons and commas.</i>"
    )


VERDICT_TITLES = {
    Verdict.APPROVED: "✅ <b>APPROVED</b>",
    Verdict.LIVE: "🔵 <b>LIVE</b> · <i>valid, not captured</i>",
    Verdict.LIVE_3DS: "🔐 <b>LIVE · 3DS REQUIRED</b>",
    Verdict.DEAD: "❌ <b>DECLINED</b>",
    Verdict.RETRY: "🟡 <b>RETRY</b>",
    Verdict.KEY_DEAD: "🟠 <b>KEY DEAD</b>",
    Verdict.GATE_FAULT: "🔴 <b>GATE FAULT</b>",
    Verdict.INCONCLUSIVE: "⚪ <b>INCONCLUSIVE</b>",
}


def result_text(outcome: CheckOutcome, *, card_line: str = "") -> str:
    title = VERDICT_TITLES.get(outcome.verdict, esc(outcome.verdict.value))
    lines = [
        f"{title} · <b>{esc(outcome.gate_label or '—')}</b> · <b>{latency(outcome.latency_ms)}</b>",
        "",
    ]
    if card_line:
        lines.append(f"<code>{esc(card_line)}</code>")
    lines.append(f"<code>{esc(outcome.pan_masked)}</code>")
    if outcome.decline_code:
        lines.append(f"🏷️ decline <code>{esc(outcome.decline_code)}</code>")
    lines.append(f"🟠 <code>{esc(outcome.code or 'none')}</code>")
    if outcome.provider_ref:
        lines.append(f"🆔 <code>{esc(outcome.provider_ref[:24])}</code>")
    if outcome.avs or outcome.cvc or outcome.three_ds:
        lines.append(
            f"📮 AVS <b>{esc(outcome.avs or '—')}</b> · "
            f"🔢 CVC <b>{esc(outcome.cvc or '—')}</b> · "
            f"🔐 3DS <b>{esc(outcome.three_ds or '—')}</b>"
        )
    if outcome.verdict is Verdict.LIVE_3DS and outcome.acs_url:
        lines.append(f'🔗 <a href="{esc(outcome.acs_url)}">Open challenge</a>')
    if outcome.debited > 0:
        lines.append(f"💠 −{fmt_cr(outcome.debited)} cr · <b>{fmt_cr(outcome.balance)}</b> left")
    else:
        lines.append("💠 <b>−0 cr</b> · <i>free on failure</i>")
    if outcome.reason:
        lines.append(f"<blockquote>{esc(outcome.reason)}</blockquote>")
    if len(outcome.attempts) > 1:
        chain = " → ".join(f"{esc(a.label)}: {esc(a.verdict.value)}" for a in outcome.attempts)
        lines.append(f"<i>{chain}</i>")
    return "\n".join(lines)


def kill_text(outcome: KillOutcome) -> str:
    lines = [
        f"🔪 <b>KILL · {esc(outcome.gate_label or '—')}</b>",
        "<blockquote>Manual-capture ladder. Stops on approve. Holds auto-cancelled.</blockquote>",
        "",
    ]
    for rung in outcome.rungs:
        lines.append(
            f"{dot('HEALTHY' if rung.verdict is Verdict.APPROVED else 'DEGRADED')} "
            f"${rung.amount_cents / 100:.2f} · {latency(rung.latency_ms)} · "
            f"<code>{esc(rung.code)}</code>"
        )
    lines.append("")
    lines.append(
        f"<b>{esc(outcome.verdict.value)}</b> · reached ${outcome.reached_cents / 100:.2f}"
    )
    if outcome.debited > 0:
        lines.append(f"💠 −{fmt_cr(outcome.debited)} cr · <b>{fmt_cr(outcome.balance)}</b> left")
    else:
        lines.append("💠 <b>0 cr</b> · <i>nothing settled</i>")
    if outcome.reason:
        lines.append(f"<i>{esc(outcome.reason)}</i>")
    return "\n".join(lines)


def bin_text(info: BinInfo, best_gates: list[str]) -> str:
    three_ds = "YES" if info.three_ds.upper() == "YES" else "UNKNOWN"
    best = ", ".join(best_gates) if best_gates else "—"
    lines = [
        f"🔍 <b>BIN {esc(info.bin[:6])}</b>",
        f"🏷️ {esc(info.brand.upper())} · {esc(info.type.upper())} · 🏦 {esc(info.bank)} · "
        f"🌍 {esc(info.iso2 or info.country)}",
        f"🔐 3DS <b>{three_ds}</b> · 💳 prepaid <b>{'yes' if info.prepaid else 'no'}</b> · "
        f"🏢 commercial <b>{'yes' if info.commercial else 'no'}</b>",
        f"📚 sources <b>{info.sources}/2</b> · ✅ <b>{info.confidence}%</b>",
        f"🛡️ best: <b>{esc(best)}</b>",
    ]
    if info.below_threshold:
        lines.append("<blockquote>⚠️ Low confidence — advisory only, never a block.</blockquote>")
    return "\n".join(lines)


def status_text(gates: list[GateView], now: datetime | None = None) -> str:
    up = [g for g in gates if g.state == "HEALTHY"]
    degraded = [g for g in gates if g.state in ("DEGRADED", "HALF_OPEN")]
    open_gates = [g for g in gates if g.state == "OPEN"]
    lines = [
        "📊 <b>GATE STATUS</b> · <i>live</i>",
        "",
        f"🟢 <b>{len(up)} up</b> · 🟡 <b>{len(degraded)} degraded</b> · "
        f"🔴 <b>{len(open_gates)} open</b>",
        "",
    ]
    for gate in degraded + open_gates:
        lines.append(f"{dot(gate.state)} {esc(gate.label)} — {latency(gate.p50_ms)}")
    for gate in up[:5]:
        success = f"{gate.success_pct:.1f}%" if gate.success_pct is not None else "—"
        lines.append(f"🟢 {esc(gate.label)} — {success} · {latency(gate.p50_ms)}")
    lines.append("")
    lines.append(
        "<blockquote>Degraded stays routable at full honesty. "
        "Open stays visible with a reason.</blockquote>"
    )
    return "\n".join(lines)


def profile_text(
    *, username: str, user_id: int, credits: Decimal, tier: str, checks: int, sticky: str
) -> str:
    return (
        f"👤 <b>PROFILE</b>\n\n"
        f"🆔 <code>{user_id}</code> · @{esc(username)}\n"
        f"💠 <b>{fmt_cr(credits)}</b> cr · 🎖️ <b>{esc(tier.upper())}</b>\n"
        f"🧾 checks <b>{checks}</b> · 🏁 sticky <b>{esc(sticky or 'auto')}</b>"
    )


def card_line(card: Card) -> str:
    return f"{card.number}|{card.exp_month:02d}|{card.exp_year % 100:02d}|{card.cvv}"
