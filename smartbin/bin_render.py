"""Section 17 BIN screens, ported from ``app/tg/render.py``.

This module is a straight port, not a second design. The legacy bot imports it
from ``smartbin`` while ``app/`` remains the source of truth; when the cutover
happens this file and ``bin_shim.py`` are deleted and ``app/`` is the only copy.

Only the database-free renderer lives here. ``GateView``/``route`` are copied
from ``app.routing.router`` because that module imports SQLAlchemy, which the
legacy Render build does not install.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape

from app.bin import confidence
from app.bin.model import BinRecord, ThreeDSStat, age_hours
from app.bin.providers import BOOL_FIELDS
from app.core.parse import fmt_cr

BIN_SCREEN_VERSION = "7.3.0"
AVS_OK = frozenset({"US", "CA", "GB"})

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

STATE_RANK = {"HEALTHY": 0, "DEGRADED": 1, "HALF_OPEN": 2, "OPEN": 3}


@dataclass(frozen=True, slots=True)
class GateView:
    id: str
    label: str
    lane: str
    processor: str
    countries: tuple[str, ...]
    cost_cr: Decimal
    amount_cents: int
    ladder: tuple[int, ...]
    state: str
    p50_ms: int | None
    p95_ms: int | None
    success_pct: float | None
    enabled: bool
    handles_3ds: bool
    needs_postal: bool
    key_country: str
    blurb: str
    sort_hint: int

    @property
    def is_open(self) -> bool:
        return self.state == "OPEN"

    @property
    def state_rank(self) -> int:
        return STATE_RANK.get(self.state, 3)


def matches_country(gate: GateView, iso2: str) -> bool:
    if not iso2:
        return True
    countries = gate.countries
    return "*" in countries or iso2.upper() in {c.upper() for c in countries}


def route(
    gates: Sequence[GateView],
    card_iso2: str,
    lane: str,
    gate_id: str | None = None,
) -> list[GateView]:
    candidates = [
        gate
        for gate in gates
        if gate.enabled and gate.lane == lane and matches_country(gate, card_iso2)
    ]
    candidates = [gate for gate in candidates if gate.state != "OPEN"]
    if gate_id:
        return sorted(candidates, key=lambda gate: (gate.id != gate_id, gate.state_rank))
    return sorted(candidates, key=lambda gate: (gate.state_rank, gate.p50_ms or 9999))


def esc(value: object) -> str:
    return escape(str(value), quote=False)


def flag(iso2: str) -> str:
    code = (iso2 or "").upper()
    if len(code) != 2 or not code.isalpha():
        return "🌍"
    return chr(0x1F1E6 + ord(code[0]) - 65) + chr(0x1F1E6 + ord(code[1]) - 65)


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
    stat: ThreeDSStat, now: datetime | None = None, *, hint: bool = False
) -> str:
    """Render the two channels honestly. Never the word UNKNOWN.

    The legacy bot has no ``measure_3ds`` port yet, so ``hint`` defaults to
    false: an unmeasured BIN shows ``UNMEASURED`` without a dead-end button.
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
    if ranked:
        return " · ".join(esc(_gate_line(gate)) for gate in ranked)
    if any(gate.enabled for gate in gates):
        return "none healthy"
    return "none enabled"


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
    record: BinRecord,
    conf: int,
    chip: str,
    word: str,
    gates: Sequence[GateView] = (),
    now: datetime | None = None,
) -> str:
    """The main BIN screen. One confidence chip, no per-field rows, one gate block."""
    current = now or datetime.now(UTC)
    gate_list = list(gates)
    lines = [f"🔍 BIN {esc(record.bin)} 📶 {conf}%  {chip}"]

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
    lines.append(f"🛡️ {pad('BEST GATES')}{fmt_gates(record, gate_list)}")
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
    ttl = f"valid {record.ttl_days}d" if record.ttl_days > 0 else "permanent"
    lines.append(
        f"👁️ {pad('SEEN')}{seconds_ago(record.fetched_at, current)}   ·   "
        f"📚 {record.sources_n} source{'s' if record.sources_n != 1 else ''}   ·   {ttl}"
    )
    lines.append("")
    lines.append(f"🏷️ SMARTBIN v{BIN_SCREEN_VERSION}-legacy · {utc_stamp(current)}")
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
            f"({seconds_ago(record.fetched_at, current)} ago) · "
            f"{'ttl ' + str(record.ttl_days) + 'd' if record.ttl_days > 0 else 'permanent'}",
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
        lines.append(
            "<blockquote>3DS is unmeasured. A real check is the only exact read.</blockquote>"
        )
    elif stale:
        lines.append("<blockquote>Record is older than its TTL. Refresh the sources.</blockquote>")
    else:
        lines.append("<blockquote>Record is complete enough to route.</blockquote>")
    return "\n".join(lines)
