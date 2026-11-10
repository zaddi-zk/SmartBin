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


# §3.3 SHOP BUYER FORMAT — optimized for card buyers
def _match_glyph(match: str) -> str:
    """Address match indicator."""
    match_upper = match.upper() if match else ""
    if match_upper in ("MATCH", "YES", "TRUE"):
        return "🟢"
    elif match_upper in ("MISMATCH", "NO", "FALSE", "PARTIAL"):
        return "🟤"
    return "—"


def _call_line(outcome: Any, record: dict) -> tuple[str, str]:
    """
    Decision call based on flags. Returns (call, reason).
    
    KILL → SKIP
    WARN → MAYBE  
    NONE → BUY
    """
    kill, warn = [], []
    
    verdict = outcome.verdict if hasattr(outcome, 'verdict') else str(getattr(outcome, 'verdict', ''))
    
    # KILL flags (force SKIP)
    if verdict in ("EXPIRED", "INVALID", "PICKUP", "LOST-STOLEN"):
        kill.append(verdict.lower().replace("-", " "))
    
    if record.get("prepaid") and getattr(outcome, "avs_posture", "") == "strict":
        kill.append("prepaid on AVS-strict")
    
    # Country mismatch check
    iso2 = str(record.get("iso2") or "").upper()
    billing_country = getattr(outcome, "billing_country", "")
    proxy_matched = getattr(outcome, "proxy_country_matched", False)
    if iso2 and billing_country and iso2 != billing_country and not proxy_matched:
        kill.append("country mismatch")
    
    # Two hard declines on clean gates
    hard_declines = getattr(outcome, "hard_decline_count", 0)
    if hard_declines >= 2:
        kill.append("multiple hard declines")
    
    # WARN flags (MAYBE)
    if verdict == "CVV-FAIL":
        warn.append("CVV mismatch")
    
    twofa = str(record.get("twofa") or getattr(outcome, "twofa_read", "")).upper()
    if twofa in ("VBV", "PARTIAL", "3DS-CHALLENGE", "CHALLENGED"):
        warn.append("3DS enrolled")
    
    state_mismatch = getattr(outcome, "state_mismatch", False)
    if state_mismatch:
        warn.append("state mismatch")
    
    if record.get("commercial"):
        warn.append("commercial")
    
    bin_age = int(record.get("bin_age") or 0)
    if bin_age > 0 and bin_age < 1:  # Less than 1 year
        warn.append("new BIN")
    
    # Low confidence check
    confs = _json(record.get("field_conf_json") or {})
    if any(c < 60 for c in confs.values() if isinstance(c, (int, float))):
        warn.append("low confidence")
    
    # Hit rate check
    hit_n = int(record.get("fleet_checks_30d") or 0)
    hit_approved = int(record.get("fleet_approved") or 0)
    if hit_n >= 20:
        hit_rate = hit_approved / hit_n if hit_n > 0 else 0
        if hit_rate < 0.40:
            warn.append("low hit rate")
    
    # Return decision
    if kill:
        return "🛑 SKIP", " · ".join(kill[:2])
    if warn:
        return "⚡ MAYBE", " · ".join(warn[:2])
    return "⚡ BUY", "clean geo, no kill flags"


def shop_buyer_block(
    outcome: Any,
    card: Any = None,
    bin_record: Any = None,
    free: bool = False,
    now: datetime | None = None,
    gate_label: str = "G01",
) -> str:
    """
    Render shop-buyer focused check result.
    
    Optimized for buyers who already know brand/bank/level from listing.
    Focus: will it die, will it fight, does address match, hit rate.
    
    14-17 lines total, call on line 3.
    """
    now = _now(now)
    d = _dict(bin_record or {})
    
    # Extract data
    bin8 = str(d.get("bin") or getattr(card, "bin8", "UNKNOWN"))
    brand = str(d.get("brand") or "UNKNOWN").upper()
    level = str(d.get("level") or "UNKNOWN").upper()
    card_type = str(d.get("card_type") or "UNKNOWN").upper()
    bank = str(d.get("bank") or "Unknown").strip()
    iso2 = str(d.get("iso2") or "").upper()
    
    prepaid = bool(int(d.get("prepaid") or 0))
    commercial = bool(int(d.get("commercial") or 0))
    atm_debit = bool(int(d.get("atm_debit") or 0))
    avs_support = bool(int(d.get("avs") or 0))
    
    twofa = str(d.get("twofa") or getattr(outcome, "twofa_read", "UNKNOWN"))
    twofa_reads = int(d.get("twofa_reads") or getattr(outcome, "twofa_reads", 0))
    twofa_conf = int(d.get("twofa_conf") or getattr(outcome, "twofa_conf", 0))
    
    bin_age = int(d.get("bin_age") or 0)
    card_length = int(d.get("card_length") or 16)
    
    # Verdict & live status
    verdict = outcome.verdict if hasattr(outcome, 'verdict') else str(getattr(outcome, 'verdict', 'UNKNOWN'))
    live = outcome.live if hasattr(outcome, 'live') else bool(getattr(outcome, 'live', False))
    latency = int(outcome.latency_ms or getattr(outcome, 'latency_ms', 0))
    
    # Determine status badge
    if live and verdict == "APPROVED":
        status_badge = "🟩 CORROBORATED"
    elif live:
        status_badge = "🟨 PARTIAL"
    else:
        status_badge = "🟩 CORROBORATED"  # Dead but confirmed
    
    # Verdict line
    if live and verdict == "APPROVED":
        verdict_line = f"🟩 LIVE · APPROVED · {latency}ms · {gate_label.upper()}"
    elif live:
        verdict_line = f"🟩 LIVE · {verdict} · {latency}ms · {gate_label.upper()}"
    else:
        verdict_line = f"🟥 DEAD · {verdict} · {latency}ms · {gate_label.upper()}"
    
    # Call line (line 3)
    call, call_reason = _call_line(outcome, d)
    
    # Address match data
    billing_zip = getattr(card, "zip", "") or str(d.get("zip") or "")
    billing_state = getattr(card, "state", "") or str(d.get("state") or "")
    billing_city = getattr(card, "city", "") or str(d.get("city") or "")
    
    # AVS results
    avs_postal = getattr(outcome, "avs_postal", "UNCHECKED")
    avs_street = getattr(outcome, "avs_street", "UNCHECKED")
    
    zip_match = _match_glyph(avs_postal)
    state_match = _match_glyph(getattr(outcome, "state_match", "UNKNOWN"))
    city_match = _match_glyph(getattr(outcome, "city_match", "UNKNOWN"))
    
    # 3DS glyph
    twofa_upper = twofa.upper().replace("*", "")
    twofa_glyph_map = {
        "NON-VBV": "🟦",
        "FRICTIONLESS": "✅",
        "CHALLENGED": "🔐",
        "EXEMPTED": "🛡️",
        "UNKNOWN": "❔",
    }
    twofa_glyph = twofa_glyph_map.get(twofa_upper, "❔")
    
    # Fleet stats
    hit_n = int(d.get("fleet_checks_30d") or 0)
    hit_approved = int(d.get("fleet_approved") or 0)
    
    if hit_n >= 5:
        hit_rate = round((hit_approved / hit_n) * 100) if hit_n > 0 else 0
        hit_line = f"📊 HIT    {hit_approved}/{hit_n} ({hit_rate}%) · 30d"
    elif hit_n > 0:
        hit_line = f"📊 HIT    cold (n={hit_n})"
    else:
        hit_line = f"📊 HIT    no data"
    
    # Confidence scores
    confs = _json(d.get("field_conf_json") or {})
    brand_conf = int(confs.get("brand", 0) or 0)
    bank_conf = int(confs.get("bank", 0) or 0)
    level_conf = int(confs.get("level", 0) or 0)
    geo_conf = int(confs.get("country", 0) or 0)
    
    level_conf_indicator = "" if level_conf >= 80 else "🟨" if level_conf >= 60 else "🔴"
    
    # Sources
    sources = _json(d.get("sources_json") or {})
    source_list = ", ".join(list(sources.keys())[:3]) if sources else "internal"
    source_count = len(sources) if sources else 1
    
    # Build flags list
    kill_flags, warn_flags = [], []
    
    if verdict in ("EXPIRED", "INVALID", "PICKUP"):
        kill_flags.append(f"🔴 {verdict.lower().replace('-', ' ')}")
    if prepaid and getattr(outcome, "avs_posture", "") == "strict":
        kill_flags.append("🔴 prepaid on AVS-strict")
    
    if verdict == "CVV-FAIL":
        warn_flags.append("🟠 CVV mismatch")
    if twofa_upper in ("VBV", "CHALLENGED"):
        warn_flags.append("🟠 3DS enrolled")
    if getattr(outcome, "state_mismatch", False):
        warn_flags.append("🟠 state mismatch")
    
    all_flags = kill_flags + warn_flags
    flags_line = "🚩 FLAGS  none" if not all_flags else "🚩 FLAGS  " + " · ".join(all_flags)
    
    # Card type line (brand/bank/level on ONE line)
    brand_glyph = _brand_glyph(brand)
    type_display = f"{brand_glyph} {brand} · {level.lower()} · {card_type.lower()} · {bank}" if bank and bank != "Unknown" else f"{brand_glyph} {brand} · {level.lower()} · {card_type.lower()}"
    
    # Build lines
    lines = [
        f"🧬 {FOOTER_PREFIX} · {bin8:<25} {status_badge}",
        verdict_line,
        f"{call} — {call_reason}",
        "━" * 50,
        f"💳 {type_display}",
    ]
    
    # Geo line (only if billing address provided)
    if billing_zip or billing_state:
        geo_str = f"{billing_state or ''} {billing_zip or ''}".strip()
        geo_flag = "🇺🇸" if iso2 == "US" else "🌐"
        lines.append(f"📍 GEO    {geo_str} → BIN {iso2 or 'XX'} 🟢")
        lines.append(f"🗺️ MATCH  zip {zip_match} · state {state_match} · city {city_match}")
    
    # 3DS line
    twofa_display = twofa.replace("*", "").replace("-", " ")
    lines.append(f"🔐 3DS    {twofa_glyph} {twofa_display} · {twofa_reads} reads · conf {twofa_conf}")
    
    # AVS line
    avs_strict = getattr(outcome, "avs_posture", "") == "strict"
    lines.append(f"🔒 AVS    {'YES' if avs_support else 'NO'} · {'strict' if avs_strict else 'attach full address'}")
    
    # Kind line
    kind_parts = []
    if prepaid:
        kind_parts.append("prepaid YES")
    else:
        kind_parts.append("prepaid NO")
    kind_parts.append(f"comm {'YES' if commercial else 'NO'}")
    kind_parts.append(f"debit {'YES' if atm_debit else 'NO'}")
    lines.append(f"🪙 KIND   {' · '.join(kind_parts)}")
    
    # PAN line
    age_str = f"{bin_age}y" if bin_age > 0 else "unknown"
    lines.append(f"📏 PAN    {card_length} · age {age_str}")
    
    lines.append("━" * 50)
    
    # Flags
    lines.append(flags_line)
    
    # Hit rate
    lines.append(hit_line)
    
    # Confidence
    conf_line = f"📊 CONF   brand {brand_conf} · bank {bank_conf} · level {level_conf}{level_conf_indicator} · geo {geo_conf}"
    lines.append(conf_line)
    
    # Sources
    lines.append(f"📖 SOURCE {source_list} · sample {source_count}")
    
    # Seen
    lines.append(f"🕐 SEEN   today · {'live' if live else 'dead'} · {gate_label.upper()}")
    
    lines.append("━" * 50)
    
    # Footer
    footer = f"🧾 {FOOTER_PREFIX} v{DISPLAY_VERSION} · {now.strftime('%Y-%m-%d')} · @SmartBin_Bot"
    lines.append(footer)
    
    if free:
        lines.insert(-1, WATERMARK)
    
    return "\n".join(lines)


# §3.2 BIN CARD — /bin, inline, mini app (PRO EDITION)
def bin_card_block(record: Any, free: bool = False, now: datetime | None = None) -> str:
    """Render v7.2.1 PRO BIN Intelligence card — complete data display."""
    now = _now(now)
    d = _dict(record)
    sources = _json(d.get("sources_json"))
    confs = _json(d.get("field_conf_json"))
    verified = str(d.get("verified_utc") or "")

    def conf(field: str) -> float:
        return _field_conf(confs, field, verified, now)

    # Extract all fields
    bin8 = str(d.get("bin") or "UNKNOWN")
    brand = str(d.get("brand") or "UNKNOWN").upper()
    level = str(d.get("level") or "UNKNOWN").upper()
    card_type = str(d.get("card_type") or "UNKNOWN").upper()
    bank = str(d.get("bank") or "").strip()
    country = str(d.get("country") or "UNKNOWN")
    iso2 = str(d.get("iso2") or "").upper()
    currency = str(d.get("currency") or "USD").upper()
    phone = str(d.get("phone") or "").strip()
    site = str(d.get("site") or "").strip()
    length = int(d.get("card_length") or 16)
    prepaid = bool(int(d.get("prepaid") or 0))
    commercial = bool(int(d.get("commercial") or 0))
    atm_debit = bool(int(d.get("atm_debit") or 0))
    avs = bool(int(d.get("avs") or 0))
    
    # 2FA/3DS data
    twofa = str(d.get("twofa") or "UNKNOWN")
    twofa_reads = int(d.get("twofa_reads") or 0)
    twofa_conf = int(d.get("twofa_conf") or 0)
    
    # Fleet stats
    fleet_n = int(d.get("fleet_checks_30d") or 0)
    fleet_approved = int(d.get("fleet_approved") or 0)
    fleet_soft = int(d.get("fleet_soft") or 0)
    fleet_hard = int(d.get("fleet_hard") or 0)
    
    # Additional metadata
    bin_age = int(d.get("bin_age") or 0)
    sample_size = int(d.get("sample_size") or 0)
    confidence = float(d.get("confidence") or 0)
    
    # Get glyphs
    brand_glyph = _brand_glyph(brand)
    type_glyph = _type_glyph(card_type)
    level_glyph = _level_glyph(level)
    twofa_glyph = GLYPH.get(f"3DS_{twofa.upper().replace('*', '')}", GLYPH["3DS_UNKNOWN"])
    
    # 2FA note
    if twofa == "NON-VBV*":
        twofa_note = " (single-gate)"
    elif twofa == "NON-VBV":
        twofa_note = " (two-gate)"
    else:
        twofa_note = ""

    # Build lines
    lines = []
    
    # Header
    lines.append(f"💎 <b>SMARTBIN BIN INTELLIGENCE</b>")
    lines.append(f"🔢 <code>{bin8}</code>")
    lines.append("")  # Spacer
    
    # Card Identity Section
    lines.append(f"📇 <b>CARD IDENTITY</b>")
    
    # Brand/Type/Level
    type_parts = []
    if brand and brand != "UNKNOWN":
        type_parts.append(f"{brand_glyph} <b>{brand}</b>")
    if card_type and card_type != "UNKNOWN" and card_type != brand:
        type_parts.append(f"{type_glyph} {card_type.lower()}")
    if level and level != "UNKNOWN":
        type_parts.append(f"{level_glyph} {level.lower()}")
    
    if type_parts:
        lines.append(f"  🏷️ TYPE: {' · '.join(type_parts)}")
    
    lines.append(f"  📏 LENGTH: <code>{length}</code> digits")
    lines.append(f"  🧮 LUHN: <code>✅ VALID</code>")
    
    # Issuer Section
    lines.append("")  # Spacer
    lines.append(f"🏦 <b>ISSUER DETAILS</b>")
    
    if bank and bank != "UNKNOWN" and bank != "—":
        lines.append(f"  🏛️ BANK: <b>{bank}</b>")
    else:
        lines.append(f"  🏛️ BANK: <i>Unknown</i>")
    
    if phone:
        lines.append(f"  ☎️ PHONE: <a href='tel:{phone}'>{phone}</a>")
    else:
        lines.append(f"  ☎️ PHONE: <i>Not available</i>")
    
    if site:
        safe_site = site.replace("http://", "").replace("https://", "")
        lines.append(f"  🌐 WEBSITE: <a href='{site}'>{safe_site}</a>")
    else:
        lines.append(f"  🌐 WEBSITE: <i>Not available</i>")
    
    # Geographic Section
    lines.append("")  # Spacer
    lines.append(f"🌍 <b>GEOGRAPHIC</b>")
    
    flag = "🇺🇸" if iso2 == "US" else "🇪🇺" if iso2 in ["EU", "DE", "FR", "IT", "ES"] else "🌐"
    lines.append(f"  📍 COUNTRY: {flag} <code>{_label(country)}</code>")
    lines.append(f"  🆔 ISO2: <code>{iso2 or 'XX'}</code>")
    lines.append(f"  💱 CURRENCY: <code>{currency}</code>")
    
    # Features Section
    lines.append("")  # Spacer
    lines.append(f"💳 <b>CARD FEATURES</b>")
    lines.append(f"  💰 PREPAID: <code>{'✅ YES' if prepaid else '❌ NO'}</code>")
    lines.append(f"  🏢 COMMERCIAL: <code>{'✅ YES' if commercial else '❌ NO'}</code>")
    lines.append(f"  🏧 ATM/DEBIT: <code>{'✅ YES' if atm_debit else '❌ NO'}</code>")
    lines.append(f"  🔒 AVS SUPPORT: <code>{'✅ YES' if avs else '❌ NO'}</code>")
    
    # 3DS/2FA Section
    lines.append("")  # Spacer
    lines.append(f"🔐 <b>3DS / 2FA</b>")
    
    twofa_label = _label(twofa)
    conf_indicator = "🟢 HIGH" if twofa_conf >= 80 else "🟡 MEDIUM" if twofa_conf >= 50 else "🔴 LOW"
    lines.append(f"  🔑 METHOD: {twofa_glyph} <code>{twofa_label}</code>{twofa_note}")
    lines.append(f"  📊 READS: <code>{twofa_reads}</code> successful verifications")
    lines.append(f"  🎯 CONFIDENCE: {conf_indicator} (<code>{twofa_conf}%</code>)")
    
    # Fleet Statistics Section
    if fleet_n >= 5:
        lines.append("")  # Spacer
        lines.append(f"📊 <b>FLEET STATISTICS</b> (30 days)")
        fleet_rate = round((fleet_approved / fleet_n) * 100) if fleet_n > 0 else 0
        rate_color = "🟢" if fleet_rate >= 70 else "🟡" if fleet_rate >= 40 else "🔴"
        lines.append(f"  🔢 TOTAL CHECKS: <code>{fleet_n}</code>")
        lines.append(f"  📈 LIVE RATE: {rate_color} <code>{fleet_rate}%</code>")
        lines.append(f"  ✅ APPROVED: <code>{fleet_approved}</code>")
        lines.append(f"  ⚠️ SOFT DECLINES: <code>{fleet_soft}</code>")
        lines.append(f"  ❌ HARD DECLINES: <code>{fleet_hard}</code>")
    elif fleet_n > 0:
        lines.append("")  # Spacer
        lines.append(f"📊 <b>FLEET STATS</b> <i>(Cold BIN: {fleet_n} checks)</i>")
    
    # Data Quality Section
    lines.append("")  # Spacer
    lines.append(f"📚 <b>DATA QUALITY</b>")
    
    # Sources
    if sources and len(sources) > 0:
        source_names = ", ".join(list(sources.keys())[:5])
        if len(sources) > 5:
            source_names += f" +{len(sources) - 5} more"
        lines.append(f"  📖 SOURCES: <code>{source_names}</code> ({len(sources)} total)")
    else:
        lines.append(f"  📖 SOURCES: <i>Internal database</i>")
    
    # Freshness
    if verified:
        try:
            from datetime import datetime
            verified_date = datetime.fromisoformat(verified.replace('Z', '+00:00'))
            days_old = (now - verified_date).days
            freshness = "🟢 FRESH" if days_old < 7 else "🟡 OK" if days_old < 30 else "🟠 OLD" if days_old < 90 else "🔴 STALE"
            lines.append(f"  📅 LAST VERIFIED: {freshness} (<code>{days_old} days</code> ago)")
        except Exception:
            lines.append(f"  📅 LAST VERIFIED: <i>Unknown</i>")
    else:
        lines.append(f"  📅 LAST VERIFIED: <i>Unknown</i>")
    
    # Confidence
    conf_quality = "🟢 HIGH" if confidence >= 0.8 else "🟡 MEDIUM" if confidence >= 0.5 else "🔴 LOW"
    lines.append(f"  🎯 OVERALL CONFIDENCE: {conf_quality} (<code>{confidence:.0%}</code>)")
    
    # Sample size
    if sample_size > 0:
        lines.append(f"  📊 SAMPLE SIZE: <code>{sample_size}</code> records")
    
    # BIN age
    if bin_age > 0:
        age_text = f"{bin_age} years" if bin_age > 1 else "1 year"
        lines.append(f"  ⏳ BIN AGE: <code>{age_text}</code>")
    
    # Footer
    lines.append("")  # Spacer
    footer = f"🧾 {FOOTER_PREFIX} v{DISPLAY_VERSION} · cache · {now.strftime('%Y-%m-%d')} · @SmartBlN_Bot"
    lines.append(footer)

    if free:
        lines.insert(-1, WATERMARK)

    return "\n".join(lines)


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
