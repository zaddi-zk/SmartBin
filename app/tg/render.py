"""HTML rendering for every screen. All dynamic text is escaped (section 3.10)."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from decimal import Decimal
from html import escape

from app.bin import confidence
from app.bin.model import BinRecord, ThreeDSStat, age_hours
from app.bin.providers import BOOL_FIELDS
from app.core.parse import Card, fmt_cr
from app.core.verdicts import Verdict
from app.exec.check import CheckOutcome
from app.exec.kill import KillOutcome
from app.routing.router import GateView, route

BIN_SCREEN_VERSION = "7.3.0"
# AVS is only meaningful where the issuer/network actually returns a postal
# check. The suffix "derived from issuer country" is mandatory on every screen.
AVS_OK = frozenset({"US", "CA", "GB"})

# One canonical glyph per attribute. A screen must never show the same icon
# twice; tests assert that.
ICONS: dict[str, str] = {
    "TYPE": "🧾",
    "COMMERCIAL": "🏢",
    "DEBIT": "🏧",
    "PREPAID": "🪙",
    "TIER": "🎖",
}

PROBE_TOOLTIP = (
    "probe uses a funded card of the same country, not this BIN — "
    "passive reads from real checks are exact."
)


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


def seconds_ago(moment: datetime | None, now: datetime | None = None) -> str:
    if moment is None:
        return "never"
    current = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    delta = int((current - moment).total_seconds())
    if delta < 0:
        return "now"
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86_400:
        return f"{delta // 3600}h"
    return f"{delta // 86_400}d"


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
        f"🔌 Proxy: <b>{esc(proxy_label)}</b> · 🏁 Gate: <b>{esc(sticky_label)}</b>\n\n"
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


def dw(text: str) -> int:
    """Display width: CJK and wide emoji count as two cells."""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def pad(label: str, width: int = 15) -> str:
    return label + " " * max(0, width - dw(label))


def padv(value: str, width: int = 16) -> str:
    """Pad a value to a fixed display width so the next column lines up."""
    return value + " " * max(0, width - dw(value))


def _times(n: int) -> str:
    if n == 1:
        return "once"
    if n == 2:
        return "twice"
    return f"{n} times"


def yn(value: bool | None) -> str:
    if value is None:
        return "?"
    return "YES" if value else "NO"


def utc_stamp(now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    return current.strftime("%Y-%m-%d %H:%MZ")


def _age_seconds(moment: datetime | None, now: datetime | None = None) -> int | None:
    if moment is None:
        return None
    current = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int((current - moment).total_seconds())


def fmt_3ds(
    stat: ThreeDSStat, now: datetime | None = None, *, hint: bool = True
) -> str:
    """Render the two channels honestly. Never the word UNKNOWN.

    Passive reads are authoritative. A probe is only a gate proxy, so it is
    labelled as such and carries the ⓘ tooltip. A probe younger than 60
    minutes is free to repeat and says so.
    """
    if stat.reads > 0:
        if stat.last_state == "YES":
            pct = round(100 * stat.challenges / stat.reads)
            text = (
                f"YES · {stat.challenges}/{stat.reads} challenged · {pct}% · "
                f"{seconds_ago(stat.last_at, now)}"
            )
        elif stat.last_state == "NO":
            pct = round(100 * (stat.reads - stat.challenges) / stat.reads)
            text = (
                f"NO · 0/{stat.reads} challenged · {pct}% · "
                f"{seconds_ago(stat.last_at, now)}"
            )
        else:
            return "N/A"
        if stat.reads < confidence.GREEN_3DS_READS:
            text += "  ⚠️ low n"
        return text

    if stat.probe_reads > 0:
        state = "PROBE YES" if stat.probe_last_state == "YES" else "PROBE NO"
        age = _age_seconds(stat.probe_last_at, now)
        if age is not None and 0 <= age < 3600:
            return (
                f"{state} · gate proxy · measured "
                f"{seconds_ago(stat.probe_last_at, now)} ago · re-probe free ⓘ"
            )
        return f"{state} · gate proxy · {seconds_ago(stat.probe_last_at, now)} ⓘ"

    return "UNMEASURED  ▶ measure" if hint else "UNMEASURED"


def _gate_line(gate: GateView) -> str:
    line = f"{gate.label} {fmt_cr(gate.cost_cr)}cr"
    if gate.handles_3ds:
        line += " (3DS)"
    return line


def best_gates(record: BinRecord, gates: list[GateView]) -> list[GateView]:
    return route(gates, record.iso2 or "", "auth")[:2]


def fmt_gates(record: BinRecord, gates: list[GateView]) -> str:
    ranked = best_gates(record, gates)
    return " · ".join(esc(_gate_line(gate)) for gate in ranked) if ranked else "—"


def record_flags(record: BinRecord) -> str:
    flags = list(record.flags)
    if record.r3ds.reads == 0:
        flags.append("unmeasured 3DS")
    elif record.r3ds.reads < confidence.GREEN_3DS_READS:
        flags.append("low 3DS reads")
    else:
        pct = round(100 * record.r3ds.challenges / record.r3ds.reads)
        if 25 <= pct <= 75:
            flags.append("3DS inconsistent")
    if record.hits.attempts >= 10 and record.hits.approved == 0:
        flags.append(f"0 hits in {record.hits.attempts}")
    return " · ".join(flags) if flags else "none"


def _range_line(record: BinRecord) -> str:
    if record.range_lo and record.range_hi:
        return f"range {esc(record.range_lo)}–{esc(record.range_hi)}"
    parent = record.parent6 or record.bin[:6]
    return f"parent {esc(parent)}"


def render_bin(
    record: BinRecord, gates: list[GateView], now: datetime | None = None
) -> str:
    """The main BIN screen. One confidence chip, no per-field rows, one gate block."""
    current = now or datetime.now(UTC)
    conf, chip, word = confidence.score(record, current)
    lines = [f"🔍 {pad('BIN')}{esc(record.bin)}        📶 {conf}%  {chip}"]

    # No passive 3DS reads means the record is not allowed to look finished,
    # even if the source score is high. The banner always renders then.
    if word != "high" or record.r3ds.reads == 0:
        reasons: list[str] = []
        if record.sources_n < 2:
            reasons.append("only one source")
        if record.conflicts:
            noun = "field" if record.conflicts == 1 else "fields"
            reasons.append(f"sources disagree on {record.conflicts} {noun}")
        if record.sample == 0:
            reasons.append("not checked yet")
        elif record.sample < 3:
            reasons.append(f"seen {_times(record.sample)}")
        if record.r3ds.reads == 0:
            reasons.append("3DS unmeasured")
        elif record.r3ds.reads < confidence.GREEN_3DS_READS:
            reasons.append(f"3DS measured {_times(record.r3ds.reads)}")
        if not reasons:
            reasons.append("limited evidence")
        lines.append(f"ℹ️  advisory · {' · '.join(reasons)}")

    if record.conflicts:
        noun = "field" if record.conflicts == 1 else "fields"
        split = (
            "sources disagree"
            if record.sources_n == 2
            else f"{record.sources_n} sources disagree"
        )
        lines.append(f"⚔️ {record.conflicts} {noun} · {split}")

    lines.append("")
    lines.append(f"🔐 {pad('3DS')}{fmt_3ds(record.r3ds, current)}")
    if record.r3ds.reads == 0 and record.r3ds.probe_reads > 0:
        lines.append(f"   <i>{esc(PROBE_TOOLTIP)}</i>")
    iso2 = (record.iso2 or "?").upper()
    lines.append(f"📍 {pad('COUNTRY')}{esc(iso2)}  {flag(iso2)}  {esc(record.country or '?')}")
    avs = "supported  full address" if iso2 in AVS_OK else "not supported"
    lines.append(f"📮 {pad('AVS')}{avs}  · derived from issuer country")
    lines.append(f"🛡️ {pad('BEST GATES')}{fmt_gates(record, gates)}")
    lines.append("")
    brand = esc((record.brand or "?").upper())
    tier = esc((record.tier or "?").upper())
    type_value = esc((record.type or "?").upper())
    lines.append(f"💳 {pad('BRAND')}{padv(brand)}{ICONS['TIER']} TIER  {tier}")
    # TYPE is the parent of PREPAID / COMMERCIAL / DEBIT. If it is unknown the
    # renderer must not assert any of the three, even if a stale row claims one.
    known_type = bool(record.type)
    commercial = record.commercial if known_type else None
    prepaid = record.prepaid if known_type else None
    debit = record.debit if known_type else None
    lines.append(
        f"{ICONS['TYPE']} {pad('TYPE')}{padv(type_value)}"
        f"{ICONS['COMMERCIAL']} COMMERCIAL  {yn(commercial)}"
    )
    lines.append(
        f"{ICONS['PREPAID']} {pad('PREPAID')}{padv(yn(prepaid))}"
        f"{ICONS['DEBIT']} DEBIT  {yn(debit)}"
    )
    lines.append(
        f"🏛 {pad('BANK')}{esc(record.bank_short or '?')}  ·  "
        f"☎️ {esc(record.bank_phone or '?')}  ·  🔗 {esc(record.bank_site or '?')}"
    )
    lines.append(
        f"🔢 {pad('PAN')}{record.pan_len or '?'}  LUHN OK  ·  {_range_line(record)}"
    )
    lines.append("")
    hits = record.hits
    hit_pct = f"{round(100 * hits.approved / hits.attempts)}%" if hits.attempts else "—"
    lines.append(
        f"🎯 {pad('HITS')}{hits.approved} / {hits.attempts}  ·  {hit_pct}  ·  "
        f"last {seconds_ago(hits.last_hit_at, current)}"
    )
    lines.append(f"🚩 {pad('FLAGS')}{esc(record_flags(record))}")
    lines.append(
        f"👁️ {pad('SEEN')}{seconds_ago(record.fetched_at, current)}   ·   "
        f"📚 {record.sources_n} sources   ·   valid {record.ttl_days}d"
    )
    lines.append("")
    lines.append(f"🏷️ SMARTBIN v{BIN_SCREEN_VERSION} · {utc_stamp(current)}")
    return "<code>" + "\n".join(lines) + "</code>"


def render_evidence(record: BinRecord, now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    conf, chip, word = confidence.score(record, current)
    lines = [f"🧠 <b>EVIDENCE</b> · {esc(record.bin)}        📶 {conf}% {chip}", ""]
    for label, delta in confidence.breakdown(record, current):
        lines.append(f"• {esc(label)}: <b>{delta:+d}</b>")
    reads = record.r3ds.reads
    lines.append(f"🔐 3DS reads <b>{reads}</b> — {'unmeasured' if reads == 0 else 'measured'}")
    lines.append(
        f"⚔️ conflicts <b>{record.conflicts}</b> — "
        f"{'nothing to cross-check' if record.conflicts == 0 else 'sources split'}"
    )
    lines.append("⚠️ advisory band" if word != "high" else "✅ high confidence")
    with_source, with_sample = confidence.projections(record, current)
    lines.extend(
        [
            "",
            f"🔧 +1 source → <b>{with_source}%</b>  ·  "
            f"+1 measurement → <b>{with_sample}%</b>",
        ]
    )
    return "\n".join(lines)


FIELD_LABELS: dict[str, str] = {
    "brand": "🏷️ brand",
    "type": f"{ICONS['TYPE']} type",
    "tier": f"{ICONS['TIER']} tier",
    "prepaid": f"{ICONS['PREPAID']} prepaid",
    "commercial": f"{ICONS['COMMERCIAL']} commercial",
    "debit": f"{ICONS['DEBIT']} debit",
    "bank_short": "🏛 bank",
    "bank_legal": "📜 legal",
    "bank_phone": "☎️ phone",
    "bank_site": "🔗 site",
    "country": "📍 country",
    "iso2": "🌐 iso2",
    "currency": "💱 currency",
    "pan_len": "🔢 pan",
    "range_lo": "⬅️ range lo",
    "range_hi": "➡️ range hi",
}


def _field_text(field: str, value: str) -> str:
    if field in BOOL_FIELDS:
        return "YES" if value.strip().lower() in ("1", "true", "yes", "y") else "NO"
    if field in ("brand", "type", "tier"):
        return value.upper()
    return value


def render_provenance(record: BinRecord, now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    lines = [f"📚 <b>PROVENANCE</b> · {esc(record.bin)}", ""]
    grouped: dict[str, list[str]] = {}
    for source in record.sources:
        grouped.setdefault(source.field, []).append(source.value)
    for field, label in FIELD_LABELS.items():
        values = grouped.get(field, [])
        if not values and field != "type":
            continue
        if not values:
            lines.append(f"{pad(label, 16)}  —    none")
            continue
        best = values[0]
        agree = sum(1 for value in values if value == best)
        field_sources = [source for source in record.sources if source.field == field]
        stored = field_sources[0].confidence if field_sources else 0
        pct = stored or round(100 * agree / len(values))
        rendered = (
            " vs ".join(esc(_field_text(field, value)) for value in values)
            if len(set(values)) > 1
            else esc(_field_text(field, best))
        )
        sources = ", ".join(source.source for source in field_sources)
        lines.append(f"{pad(label, 16)}  {pct}%  {esc(sources)}  {rendered}")
    lines.extend(
        [
            "",
            f"📅 fetched {utc_stamp(record.fetched_at)} "
            f"({seconds_ago(record.fetched_at, current)} ago) · ttl {record.ttl_days}d",
        ]
    )
    return "\n".join(lines)


def render_doctor(
    record: BinRecord, gates: list[GateView], now: datetime | None = None
) -> str:
    current = now or datetime.now(UTC)
    ranked = best_gates(record, gates)
    stale = age_hours(record.fetched_at, current) > record.ttl_days * 24
    checks = [
        ("sources", record.sources_n >= 2, f"{record.sources_n}"),
        ("conflicts", record.conflicts == 0, f"{record.conflicts}"),
        ("type", bool(record.type), (record.type or "?").upper()),
        ("3DS measured", record.r3ds.reads > 0, f"{record.r3ds.reads} reads"),
        ("fresh", not stale, f"{age_hours(record.fetched_at, current):.0f}h"),
        ("routable gate", bool(ranked), ranked[0].label if ranked else "none"),
    ]
    lines = [f"🩺 <b>DOCTOR</b> · {esc(record.bin)}", ""]
    for label, ok, detail in checks:
        lines.append(f"{'✅' if ok else '⚠️'} {esc(label)} · <b>{esc(detail)}</b>")
    lines.append("")
    if not ranked:
        lines.append("<blockquote>No healthy auth gate for this country right now.</blockquote>")
    elif record.r3ds.reads == 0:
        lines.append("<blockquote>3DS is unmeasured. A probe costs 1 cr.</blockquote>")
    elif stale:
        lines.append("<blockquote>Record is older than its TTL. Refresh the sources.</blockquote>")
    else:
        lines.append("<blockquote>Record is complete enough to route.</blockquote>")
    return "\n".join(lines)


def render_bin_gates(
    record: BinRecord, gates: list[GateView], now: datetime | None = None
) -> str:
    lines = [
        f"🛡️ <b>ALL GATES</b> · {esc(record.bin)}",
        "<blockquote>Ranked for this issuer country. Open gates stay visible.</blockquote>",
        "",
    ]
    if not gates:
        lines.append("🚧 <i>No gate seeded yet.</i>")
    for gate in sorted(gates, key=lambda item: (item.lane, item.state_rank, item.p50_ms or 9999)):
        state = "⚪" if not gate.enabled else dot(gate.state)
        lines.append(
            f"{state} <b>{esc(gate.label)}</b> · {esc(gate.lane)} · "
            f"{fmt_cr(gate.cost_cr)} cr · {latency(gate.p50_ms)}"
        )
    return "\n".join(lines)


def render_bin_share(
    record: BinRecord, gates: list[GateView], now: datetime | None = None
) -> str:
    current = now or datetime.now(UTC)
    conf, chip, _word = confidence.score(record, current)
    ranked = best_gates(record, gates)
    top = _gate_line(ranked[0]) if ranked else "no gate"
    return (
        f"🔍 BIN {esc(record.bin)} · {esc((record.brand or '?').upper())} · "
        f"{esc(record.iso2 or '?')} {flag(record.iso2 or '')}\n"
        f"📶 {conf}% {chip} · 🔐 3DS {fmt_3ds(record.r3ds, current, hint=False)}\n"
        f"🛡️ {esc(top)}"
    )


def render_bin_copy(record: BinRecord, gates: list[GateView], now: datetime | None = None) -> str:
    """The single copy affordance: a self-contained record, not a screenshot."""
    return render_bin(record, gates, now)


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
