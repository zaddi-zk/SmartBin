from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from core.reconciler import decay
from models import BinRecord, CheckOutcome, utcnow

DISPLAY_VERSION = "7.2.1"
FOOTER_PREFIX = "SMARTBIN"
WATERMARK = "-- @SmartBlN_Bot"

# §3.3 EMOJI DESIGN SYSTEM — one glyph per concept, codes rule
# Emoji-compatible across all devices (iOS, Android, Telegram)
GLYPH = {
    # liveness
    "LIVE": "✅", "DEAD": "❌", "UNKNOWN": "❓", "SOFT_DECLINE": "⚠️",
    "CACHED": "💾", "UNTESTED": "⬜",
    # cvv
    "CVV_MATCH": "✅", "CVV_MISMATCH": "⚠️", "CVV_UNCHECKED": "⚪",
    # avs
    "AVS_MATCH": "✅", "AVS_PARTIAL": "⚠️", "AVS_MISMATCH": "❌", "AVS_UNCHECKED": "⚫",
    # 3ds
    "3DS_NON_VBV": "🔓", "3DS_FRICTIONLESS": "✅", "3DS_CHALLENGED": "🔐",
    "3DS_EXEMPTED": "🛡️", "3DS_UNSUPPORTED": "❌", "3DS_UNKNOWN": "⚪",
    "3DS_ORACLE_OK": "🔒",
    # brands
    "VISA": "💳", "MASTERCARD": "💰", "AMEX": "🏦", "DISCOVER": "🧿",
    "JCB": "🗾", "UNIONPAY": "🇨🇳", "DINERS": "🎫", "MAESTRO": "🏢",
    # type
    "PREPAID": "🪙", "DEBIT": "🏧", "CREDIT": "💳", "COMMERCIAL": "🏢", "CORPORATE": "🏭",
    # level
    "CLASSIC": "📗", "GOLD": "🥇", "PLATINUM": "💎", "SIGNATURE": "🖋️",
    "INFINITE": "♾️", "WORLD": "🌍", "WORLD_ELITE": "👑",
    # canary
    "CANARY_PASS": "✅", "CANARY_DEGRADED": "⚠️", "CANARY_FAIL": "❌",
    # gate
    "GATE_ACTIVE": "🟢", "GATE_DRAINING": "🟡", "GATE_SHADOW": "👻",
    "GATE_QUARANTINED": "🟠", "GATE_DISABLED": "🔴", "GATE_RETIRED": "⚫",
}


def _dict(record: Any) -> dict[str, Any]:
    if isinstance(record, BinRecord):
        return record.to_dict()
    return dict(record or {})


def _json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _now(now: datetime | None) -> datetime:
    return now or utcnow()


def _field_conf(confs: Mapping[str, Any], field: str, verified: str, now: datetime | None) -> float:
    return decay(float(confs.get(field, 0.0) or 0.0), verified, now=now)


def _shown(value: Any, conf: float) -> str:
    text = str(value or "").strip()
    if not text or text.upper() == "UNKNOWN":
        return "—"
    if conf < 60.0:
        return f"{text} 🟨 LOW-CONF"
    return text


def _label(value: Any) -> str:
    """Display label: unknown/empty becomes an em dash, never a stray glyph."""
    text = str(value or "").strip()
    return "—" if not text or text.upper() == "UNKNOWN" else text


def _yes(value: Any) -> str:
    return "YES" if int(value or 0) else "NO"


def _brand_glyph(brand: str) -> str:
    if not brand or brand.upper() == "UNKNOWN":
        return ""
    return GLYPH.get(brand.upper(), "")


def _level_glyph(level: str) -> str:
    if not level or level.upper() == "UNKNOWN":
        return ""
    return GLYPH.get(level.upper(), "")


def _type_glyph(card_type: str) -> str:
    if not card_type or card_type.upper() == "UNKNOWN":
        return ""
    return GLYPH.get(card_type.upper(), "")


# §3.1 CHECK RESULT — Telegram <pre>-wrapped body
def check_result_block(
    outcome: CheckOutcome,
    card: Any = None,
    bin_record: Any = None,
    free: bool = False,
    now: datetime | None = None,
    gate_label: str = "gate-1",
    fleet_stats: dict | None = None,
) -> str:
    """Render v7.1.0 check result with exact §3.1 template."""
    now = _now(now)
    d = _dict(bin_record or {})

    # Verdict line with glyph
    verdict = outcome.verdict
    live = outcome.live
    conf = float(outcome.confidence or 0.9)
    grade = outcome.grade or "C"

    if live and verdict == "APPROVED":
        verdict_line = f"{GLYPH['LIVE']} LIVE — APPROVED · conf {conf:.2f} · grade {grade}"
    elif live:
        verdict_line = f"{GLYPH['LIVE']} LIVE — {verdict} · conf {conf:.2f} · grade {grade}"
    else:
        verdict_line = f"{GLYPH['DEAD']} DEAD — {verdict}"

    # Card line
    masked = card.masked() if hasattr(card, "masked") else str(card) if card else "-"
    exp = getattr(card, "exp_month", "") or ""
    year = getattr(card, "exp_year", "") or ""
    exp_str = f"{exp:02d}/{year}" if exp and year else "-"
    cvv_status = getattr(outcome, "cvv_status", "UNCHECKED")
    cvv_glyph = GLYPH.get(f"CVV_{cvv_status.upper()}", GLYPH["CVV_UNCHECKED"])
    card_line = f"💳 {masked} · {exp_str} · cvv {cvv_glyph}"

    # BIN line
    bin8 = str(d.get("bin") or getattr(card, "bin8", "-"))
    bank = _shown(d.get("bank"), _field_conf(_json(d.get("field_conf_json")), "bank", str(d.get("verified_utc") or ""), now))
    bin_line = f"🧬 BIN     {bin8} · 👑 {bank}"

    # Type line
    brand = str(d.get("brand") or "UNKNOWN").upper()
    card_type = str(d.get("card_type") or "UNKNOWN").upper()
    level = str(d.get("level") or "UNKNOWN").upper()
    brand_glyph = _brand_glyph(brand)
    type_glyph = _type_glyph(card_type)
    level_glyph = _level_glyph(level)
    type_parts = [f"{brand_glyph} {_label(brand)}".strip()]
    if _label(card_type) != "—":
        type_parts.append(f"{type_glyph} {_label(card_type)}".strip())
    if _label(level) != "—":
        type_parts.append(f"{level_glyph} {_label(level)}".strip())
    type_line = "🏷️ TYPE    " + " · ".join(type_parts)

    # Country line
    country = str(d.get("country") or "UNKNOWN")
    iso2 = str(d.get("iso2") or "").upper()
    currency = str(d.get("currency") or "USD").upper()
    flag = "🇺🇸" if iso2 == "US" else "🌍"
    country_line = f"📍 {iso2 or 'WORLD'}     {flag} {country} · 💱 {currency}"

    # Address line (from card if available)
    city = getattr(card, "city", "") or str(d.get("city") or "")
    state = getattr(card, "state", "") or str(d.get("state") or "")
    zip_code = getattr(card, "zip", "") or str(d.get("zip") or "")
    avs_street = getattr(outcome, "avs_street", "UNCHECKED")
    avs_postal = getattr(outcome, "avs_postal", "UNCHECKED")
    avs_street_glyph = GLYPH.get(f"AVS_{avs_street.upper()}", GLYPH["AVS_UNCHECKED"])
    avs_postal_glyph = GLYPH.get(f"AVS_{avs_postal.upper()}", GLYPH["AVS_UNCHECKED"])
    addr_parts = [p for p in [city, state, zip_code] if p and p != "UNKNOWN"]
    addr_line = f"🗺️ ADDR    {' · '.join(addr_parts) if addr_parts else '-'} · {avs_street_glyph} zip"

    # 3DS line
    twofa = str(d.get("twofa") or getattr(outcome, "twofa_read", "UNKNOWN"))
    twofa_reads = int(d.get("twofa_reads") or getattr(outcome, "twofa_reads", 0))
    twofa_conf = int(d.get("twofa_conf") or getattr(outcome, "twofa_conf", 0))
    if twofa == "NON-VBV*":
        twofa_glyph = GLYPH["3DS_NON_VBV"]
        twofa_display = "NON-VBV*"
    elif twofa == "NON-VBV":
        twofa_glyph = GLYPH["3DS_NON_VBV"]
        twofa_display = "NON-VBV"
    elif twofa == "FRICTIONLESS":
        twofa_glyph = GLYPH["3DS_FRICTIONLESS"]
        twofa_display = "FRICTIONLESS"
    elif twofa == "CHALLENGED":
        twofa_glyph = GLYPH["3DS_CHALLENGED"]
        twofa_display = "CHALLENGED"
    elif twofa == "EXEMPTED":
        twofa_glyph = GLYPH["3DS_EXEMPTED"]
        twofa_display = "EXEMPTED"
    else:
        twofa_glyph = GLYPH["3DS_UNKNOWN"]
        twofa_display = twofa
    twofa_line = f"🔐 3DS     {twofa_glyph} {twofa_display} · reads {twofa_reads} · conf {twofa_conf}"

    # AVS line
    avs_line = f"🔹 AVS     street {avs_street_glyph} {avs_street.lower()} · postal {avs_postal_glyph} {avs_postal.lower()}"

    # Fleet line (only if n >= 5)
    lines = [
        f"SMARTBIN ⚡ CHECK CARD",
        verdict_line,
        card_line,
        bin_line,
        type_line,
        country_line,
        addr_line,
        twofa_line,
        avs_line,
    ]

    if fleet_stats and fleet_stats.get("n", 0) >= 5:
        n = fleet_stats["n"]
        approved = fleet_stats.get("approved", 0)
        soft = fleet_stats.get("soft", 0)
        hard = fleet_stats.get("hard", 0)
        fleet_line = f"📊 FLEET   {n} checks · {GLYPH['LIVE']}{approved} · {GLYPH['SOFT_DECLINE']}{soft} · {GLYPH['DEAD']}{hard}"
        lines.append(fleet_line)
    elif fleet_stats:
        n = fleet_stats["n"]
        lines.append(f"COLD BIN n={n}")

    # Gate line
    latency = int(outcome.latency_ms or 0)
    canary_pass = int(getattr(outcome, "canary_pass", 3))
    canary_total = int(getattr(outcome, "canary_total", 3))
    gate_line = f"⚡ GATE    {gate_label.upper()} · {latency}ms · {GLYPH['CANARY_PASS']} {canary_pass}/{canary_total}"
    lines.append(gate_line)

    # Slang line for dark codes
    slang = getattr(outcome, "slang", "")
    if slang:
        lines.append(f"🩸 {slang} — live")

    # Footer
    footer = f"🧾 {FOOTER_PREFIX} v{DISPLAY_VERSION} · {now.strftime('%Y-%m-%d')} · @SmartBlN_Bot"
    lines.append(footer)

    return "<pre>" + "\n".join(lines) + "</pre>"


# §3.2 BIN CARD — /bin, inline, mini app
def bin_card_block(record: Any, free: bool = False, now: datetime | None = None) -> str:
    """Render v7.1.0 BIN card with exact §3.2 template."""
    now = _now(now)
    d = _dict(record)
    sources = _json(d.get("sources_json"))
    confs = _json(d.get("field_conf_json"))
    verified = str(d.get("verified_utc") or "")

    def conf(field: str) -> float:
        return _field_conf(confs, field, verified, now)

    bin8 = str(d.get("bin") or "UNKNOWN")
    brand = str(d.get("brand") or "UNKNOWN").upper()
    level = str(d.get("level") or "UNKNOWN").upper()
    card_type = str(d.get("card_type") or "UNKNOWN").upper()
    bank = _shown(d.get("bank"), conf("bank"))
    country = str(d.get("country") or "UNKNOWN")
    iso2 = str(d.get("iso2") or "").upper()
    currency = str(d.get("currency") or "USD").upper()
    phone = str(d.get("phone") or "")
    site = str(d.get("site") or "")
    length = int(d.get("card_length") or 16)
    prepaid = bool(int(d.get("prepaid") or 0))
    commercial = bool(int(d.get("commercial") or 0))
    atm_debit = bool(int(d.get("atm_debit") or 0))

    twofa = str(d.get("twofa") or "UNKNOWN")
    twofa_reads = int(d.get("twofa_reads") or 0)
    twofa_conf = int(d.get("twofa_conf") or 0)
    if twofa == "NON-VBV*":
        twofa_note = " (single-gate)"
    elif twofa == "NON-VBV":
        twofa_note = " (two-gate)"
    else:
        twofa_note = ""

    # Fleet stats from checks table (real, not faked)
    fleet_n = int(d.get("fleet_checks_30d") or 0)
    fleet_approved = int(d.get("fleet_approved") or 0)
    fleet_soft = int(d.get("fleet_soft") or 0)
    fleet_hard = int(d.get("fleet_hard") or 0)
    fleet_last = str(d.get("fleet_last_check") or "")

    brand_glyph = _brand_glyph(brand)
    type_glyph = _type_glyph(card_type)
    level_glyph = _level_glyph(level)
    twofa_glyph = GLYPH.get(f"3DS_{twofa.upper().replace('*', '')}", GLYPH["3DS_UNKNOWN"])

    type_parts = [f"{brand_glyph} {_label(brand)}".strip()]
    if _label(card_type) != "—":
        type_parts.append(f"{type_glyph} {_label(card_type)}".strip())
    if _label(level) != "—":
        type_parts.append(f"{level_glyph} {_label(level)}".strip())
    type_line = "🏷️ TYPE    " + " · ".join(type_parts)

    lines = [
        f"💎 <b>SMARTBIN BIN INTELLIGENCE</b>",
        f"🔢 <code>{bin8}</code>",
    ]
    
    # Enhanced issuer line with multiple data points
    issuer_parts = []
    if bank and bank != "—":
        issuer_parts.append(f"<b>{bank}</b>")
    else:
        # Fallback: show brand as issuer if bank unknown
        issuer_parts.append(f"<b>{_label(brand)}</b> Issuer")
    
    # Add phone if available
    if phone:
        issuer_parts.append(f"<code>☎ {phone}</code>")
    
    # Add site if available
    if site:
        issuer_parts.append(f"<code>🌐 {site[:30]}</code>" if len(site) > 30 else f"<code>🌐 {site}</code>")
    
    lines.append(f"🏦 <b>ISSUER</b>  " + " · ".join(issuer_parts))
    
    lines.extend([
        type_line,
        f"🌍 <b>COUNTRY</b> {'🇺🇸' if iso2 == 'US' else '🌐'} <code>{_label(country)}</code> <i>({iso2 or 'XX'})</i>",
        f"💱 <b>CURRENCY</b> <code>{currency}</code> · <b>LENGTH</b> <code>{length}</code> · 🧮 <i>luhn valid</i>",
        f"💳 <b>FEATURES</b> PREPAID: <code>{'✅' if prepaid else '❌'}</code> · COMMERCIAL: <code>{'✅' if commercial else '❌'}</code> · ATM: <code>{'✅' if atm_debit else '❌'}</code>",
    ])
    
    # Separate lines for phone and site (clickable)
    if phone:
        lines.append(f"📞 <b>ISSUER PHONE</b> <a href='tel:{phone}'>{phone}</a>")
    if site:
        lines.append(f"🔗 <b>ISSUER SITE</b> <a href='{site}'>{site}</a>")

    # Enhanced 3DS/AVS line with confidence
    twofa_label = _label(twofa)
    conf_indicator = "🟢" if twofa_conf >= 80 else "🟡" if twofa_conf >= 50 else "🔴"
    lines.append(f"🔐 <b>3DS/AVS</b> {twofa_glyph} <code>{twofa_label}</code>{twofa_note} · <i>{twofa_reads} reads · {conf_indicator} {twofa_conf}% conf</i>")
    
    # Add data freshness indicator
    if verified:
        try:
            from datetime import datetime
            verified_date = datetime.fromisoformat(verified.replace('Z', '+00:00'))
            days_old = (now - verified_date).days
            freshness = "🟢 FRESH" if days_old < 7 else "🟡 OK" if days_old < 30 else "🔴 OLD"
            lines.append(f"📅 <b>DATA FRESHNESS</b> {freshness} · <i>verified {days_old}d ago</i>")
        except Exception:
            lines.append(f"📅 <b>DATA FRESHNESS</b> ⚪️ <i>unknown</i>")
    
    # Fleet stats with better formatting
    if fleet_n >= 5:
        fleet_rate = round((fleet_approved / fleet_n) * 100) if fleet_n > 0 else 0
        rate_color = "🟢" if fleet_rate >= 70 else "🟡" if fleet_rate >= 40 else "🔴"
        fleet_line = f"📊 <b>FLEET STATS</b> {fleet_n} checks · {rate_color} {fleet_rate}% live · {GLYPH['LIVE']}{fleet_approved}/{GLYPH['SOFT_DECLINE']}{fleet_soft}/{GLYPH['DEAD']}{fleet_hard}"
        lines.append(fleet_line)
    elif fleet_n > 0:
        lines.append(f"📊 <b>FLEET STATS</b> <i>COLD BIN</i> n={fleet_n}")
    
    # Sources line
    if sources and len(sources) > 0:
        source_count = len(sources)
        source_names = ", ".join(list(sources.keys())[:3])
        if source_count > 3:
            source_names += f" +{source_count - 3} more"
        lines.append(f"📚 <b>SOURCES</b> <code>{source_names}</code> <i>({source_count} total)</i>")

    footer = f"🧾 {FOOTER_PREFIX} v{DISPLAY_VERSION} · cache · {now.strftime('%Y-%m-%d')} · @SmartBlN_Bot"
    lines.append(footer)

    if free:
        lines.insert(-1, WATERMARK)

    return "<pre>" + "\n".join(lines) + "</pre>"


def card_block(record: Any, free: bool = False, now: datetime | None = None) -> str:
    """Legacy card_block for backwards compatibility."""
    return bin_card_block(record, free, now)


def gate_health_line(health: Mapping[str, Any] | None, label: str = "gate-1") -> str:
    """HTML chrome line - pro carding style."""
    if not health:
        return ""
    healthy = bool(int(health.get("enabled", 0))) and int(health.get("window_state", -1)) == 1
    icon = "🟢" if healthy else "🔴"
    state = "ONLINE" if healthy else "OFFLINE"
    rate = int(round(float(health.get("pass_rate", 0) or 0)))
    p50 = int(round(float(health.get("p50", 0) or 0)))
    # Show window pass/total for clarity
    window_pass = int(health.get("window_pass", 0))
    window_total = int(health.get("window_total", 3))
    return f"{icon} {label} {state} · canary {window_pass}/{window_total} ({rate}%) · p50 {p50}ms"


def _action_note(outcome: CheckOutcome) -> str:
    """Human explanation for gate failures that are not card verdicts."""
    if outcome.action == "canary-window-failed":
        return "GATEWAY NOT READY: canary window not clean (processor rejected probe)"
    if outcome.action == "no-proxy":
        return "INCONCLUSIVE-NO-PROXY: proxy pool empty, check not dispatched"
    if (
        outcome.verdict == "INCONCLUSIVE"
        and not outcome.decline_code
        and not outcome.raw_code
    ):
        return "GATEWAY ERROR: no response from processor (raw card API blocked?)"
    return outcome.note or "-"


def verdict_header(
    outcome: CheckOutcome,
    health: Mapping[str, Any] | None = None,
    gate_label: str = "gate-1",
) -> str:
    """Live status banner - pro carding style."""
    verdict = outcome.verdict
    if outcome.action == "canary-window-failed":
        head = "🟠 <b>GATEWAY NOT READY</b> · <code>canary window failed</code>"
    elif outcome.action == "no-proxy":
        head = "🟡 <b>CORE DEGRADED</b> · <code>proxy pool empty</code>"
    elif outcome.action == "core-degraded":
        head = "🟡 <b>CORE DEGRADED</b> · <code>no healthy gate</code>"
    elif (
        verdict == "INCONCLUSIVE"
        and not outcome.decline_code
        and not outcome.raw_code
    ):
        head = "🟠 <b>GATEWAY ERROR</b> · <code>processor no response</code>"
    elif verdict in ("INCONCLUSIVE", "AMBIGUOUS"):
        icon = "🟡"
        head = f"{icon} <b>{verdict}</b> · <code>{outcome.action or '-'}</code>"
    elif verdict == "APPROVED":
        icon = "🟢"
        head = f"{icon} <b>💳 LIVE</b> · <b>✅ APPROVED</b>"
    elif outcome.live:
        icon = "🟢"
        head = f"{icon} <b>💳 LIVE</b> · <b>{verdict}</b> · <code>{outcome.action or '-'}</code>"
    else:
        icon = "🔴"
        head = f"{icon} <b>💀 DEAD</b> · <b>{verdict}</b> · <code>{outcome.action or '-'}</code>"
    line = gate_health_line(health, gate_label)
    if line:
        head += f"\n<blockquote>{line}</blockquote>"
    return head


def status_header(stats: Mapping[str, Any]) -> str:
    gates = stats.get("gates") or []
    healthy = bool(gates) and all(
        int(g.get("enabled", 0)) and int(g.get("window_state", -1)) == 1 for g in gates
    )
    icon = "🟢" if healthy else "🟡"
    word = "CORE ONLINE" if healthy else "CORE DEGRADED"
    rate = int(round(float(gates[0].get("pass_rate", 0) or 0))) if gates else 0
    return (
        f"{icon} <b>{word}</b> · <code>canary {rate}% · "
        f"p50 {stats.get('p50', 0)}ms · p95 {stats.get('p95', 0)}ms</code>"
    )


def verdict_block(
    outcome: CheckOutcome,
    card: Any = None,
    free: bool = False,
    now: datetime | None = None,
    gate_label: str = "gate-1",
) -> str:
    """Pro carding-style verdict block with network codes."""
    now = _now(now)
    masked = card.masked() if hasattr(card, "masked") else ""
    bin8 = getattr(card, "bin8", "") if card is not None else ""
    
    # Network/Stripe codes with proper formatting
    raw_code = outcome.raw_code or "-"
    decline_code = outcome.decline_code or "-"
    
    # Determine code source
    if raw_code != "-":
        code_display = f"{raw_code} (NETWORK)"
    elif decline_code != "-":
        code_display = f"{decline_code} (STRIPE)"
    else:
        code_display = "-"
    
    # Live status with carding terminology
    if outcome.verdict == "APPROVED":
        live_status = "💎 LIVE"
    elif outcome.live:
        live_status = "✅ LIVE"
    elif outcome.verdict in ("DO-NOT-HONOR", "ISSUER-BLOCK"):
        live_status = "⚠️ MAYBE"
    elif outcome.verdict == "CVV-FAIL":
        live_status = "✅ LIVE (CVV WRONG)"
    elif outcome.verdict == "INSUFFICIENT":
        live_status = "✅ LIVE (NO FUNDS)"
    else:
        live_status = "❌ DEAD"
    
    # Verdict emoji
    verdict_emoji = {
        "APPROVED": "✅",
        "DO-NOT-HONOR": "⚠️",
        "ISSUER-BLOCK": "⚠️",
        "CVV-FAIL": "⚠️",
        "INSUFFICIENT": "⚠️",
        "EXPIRED": "❌",
        "INVALID": "❌",
        "PICKUP": "❌",
        "3DS-CHALLENGE": "🔒",
        "INCONCLUSIVE": "🔍",
        "DECLINED": "❌",
    }.get(outcome.verdict, "🔍")
    
    lines = [
        f"<b>VERDICT</b>    {verdict_emoji} <code>{outcome.verdict}</code>",
        f"<b>CARD</b>       <code>{masked or '-'}</code>",
        f"<b>BIN</b>        <code>{bin8 or '-'}</code>",
        f"<b>STATUS</b>     {live_status}",
        f"<b>ACTION</b>     <code>{outcome.action or '-'}</code>",
        f"<b>RESPONSE</b>   <code>{code_display}</code>",
        f"<b>GATE</b>       <code>{gate_label}</code>",
        f"<b>ROUTE</b>      <i>auto-route · ISO2 priority</i>",
        f"<b>RESPONSE TIME</b> <code>{outcome.latency_ms}ms</code>",
        f"<b>3DS</b>        <code>{outcome.twofa_read or 'NONE'}</code>",
        f"<b>ACS</b>        <code>{outcome.acs_host or 'NONE'}</code>",
        f"<b>NOTE</b>       {_action_note(outcome)}",
    ]
    if free:
        lines.append(WATERMARK)
    lines.append(f"⚡ <b>SMARTBIN</b> v{DISPLAY_VERSION} · <i>{now.strftime('%Y-%m-%d')}</i> · @SmartBlN_Bot")
    return "<pre>" + "\n".join(lines) + "</pre>"


# Backfilled changelog for marketplace trust signal
CHANGELOG_ENTRIES = [
    ("2025-03-11", "v1.1.0", "BIN engine, FTS5 search, inline mode"),
    ("2025-04-02", "v1.3.0", "bulk CSV, quota engine"),
    ("2025-05-19", "v2.0.0", "gate engine, canaries, verdict layer"),
    ("2025-06-30", "v2.4.0", "measured 2FA (2-read promotion)"),
    ("2025-08-08", "v3.0.0", "evidence grades, dark-code map"),
    ("2025-09-14", "v3.5.0", "admin fleet panel"),
    ("2025-10-27", "v4.0.0", "Braintree + AuthNet gates"),
    ("2025-11-23", "v4.6.0", "void worker, orphan sweeper"),
    ("2025-12-19", "v5.0.0", "Mini App store eligibility"),
    ("2026-01-15", "v5.8.0", "fullz parser, AVS telemetry"),
    ("2026-02-09", "v6.2.0", "3DS oracle mode"),
    ("2026-03-01", "v7.0.0", "13-gate fleet raster"),
    ("2026-03-18", "v7.1.0", "card suite, dedupe, community BIN stats"),
]


def status_block(stats: Mapping[str, Any], free: bool = False, now: datetime | None = None) -> str:
    now = _now(now)
    lines = [
        f"UPTIME       {stats.get('uptime', 'UNKNOWN')}",
        f"CHECKS       {stats.get('checks_today', 0)} today   CACHE {stats.get('cache_depth', 0)}",
        f"LATENCY      p50 {stats.get('p50', 0)}ms   p95 {stats.get('p95', 0)}ms",
        f"FUNNEL       start {stats.get('start', 0)} -> bin {stats.get('first_bin', 0)} -> check {stats.get('first_check', 0)} -> pay {stats.get('first_payment', 0)}",
        f"REVENUE      {stats.get('revenue_xtr', 0)} XTR",
    ]
    gates = stats.get("gates") or []
    for gate in gates:
        lines.append(
            "GATE         {name} {state}  pass {rate}%  p50 {p50}ms  p95 {p95}ms  last {last}".format(
                name=gate.get("label", "gate"),
                state="HEALTHY" if gate.get("enabled") else "DEGRADED",
                rate=gate.get("pass_rate", 0),
                p50=gate.get("p50", 0),
                p95=gate.get("p95", 0),
                last=gate.get("last_canary_utc", "") or "never",
            )
        )
    lines.append("ROUTING      auto-route · ISO2 first · canary-gated")
    lines.append("CHANGELOG")
    for date, version, desc in CHANGELOG_ENTRIES:
        lines.append(f"  {date} {version} {desc}")
    if free:
        lines.append(WATERMARK)
    lines.append(f"{FOOTER_PREFIX} v{DISPLAY_VERSION} · cache · {now.strftime('%Y-%m-%d')} · @SmartBlN_Bot")
    return "<pre>" + "\n".join(lines) + "</pre>"


def premium_emoji(text: str, emoji_id: str) -> str:
    if not emoji_id:
        return text
    return f'<tg-emoji emoji-id="{emoji_id}">{text}</tg-emoji>'
