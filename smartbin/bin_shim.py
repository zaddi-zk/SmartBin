"""Legacy cache row -> section 17 ``BinRecord`` shim.

The legacy bot stores BINs in ``bins`` (one row per BIN) and check evidence in
``checks``. The section 17 renderer expects the ``app.bin.model`` dataclasses.
This module is the only place that knows both shapes; deleting it is the
cutover checklist item for ``app/``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.bin import confidence as app_confidence
from app.bin.model import BinRecord, FieldSource, HitStat, ThreeDSStat

try:  # imported as ``bin_shim`` by the legacy bot, or ``smartbin.bin_shim`` by tests
    from bin_render import GateView
except ImportError:  # pragma: no cover - package import path
    from smartbin.bin_render import GateView


def _row(record: Any) -> dict[str, Any]:
    if hasattr(record, "to_dict"):
        return dict(record.to_dict())
    return dict(record or {})


def _json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.upper() == "UNKNOWN":
        return None
    return text


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    return bool(_int(value))


def parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _age_days(fetched_at: datetime | None, now: datetime | None) -> int | None:
    if fetched_at is None:
        return None
    current = now or datetime.now(UTC)
    return max(0, (current - fetched_at).days)


_FIELD_MAP = {
    "level": "tier",
    "bank": "bank_short",
    "phone": "bank_phone",
    "site": "bank_site",
    "card_length": "pan_len",
    "card_type": "type",
    "atm_debit": "debit",
}


def field_sources(row: Mapping[str, Any], at: datetime | None) -> list[FieldSource]:
    sources = _json(row.get("sources_json"))
    confs = _json(row.get("field_conf_json"))
    out: list[FieldSource] = []
    for field, provenance in sources.items():
        if not isinstance(provenance, dict):
            continue
        confidence = _int(confs.get(field, 0))
        mapped = _FIELD_MAP.get(str(field), str(field))
        for name, value in provenance.items():
            out.append(
                FieldSource(
                    source=str(name),
                    field=mapped,
                    value=str(value),
                    confidence=confidence,
                    at=at,
                )
            )
    return out


def three_ds(
    row: Mapping[str, Any], *, challenges: int = 0, last_state: str = ""
) -> ThreeDSStat:
    reads = _int(row.get("twofa_reads"))
    last_at = parse_ts(row.get("last_twofa_utc"))
    state = str(last_state or "").upper()
    if reads and state not in ("YES", "NO"):
        stored = str(row.get("twofa") or "").upper()
        if stored == "VBV":
            state = "YES"
        elif stored in ("NON-VBV", "NON-VBV*", "LOW-CONF", "PARTIAL"):
            state = "NO"
        else:
            state = ""
    # A legacy DB can have more VBV rows than the aggregate counter; never
    # render a challenge rate above 100%.
    return ThreeDSStat(
        reads=reads,
        challenges=min(max(0, _int(challenges)), reads),
        last_at=last_at,
        last_state=state if reads else "",
    )


def hit_stat(row: Mapping[str, Any] | HitStat | None) -> HitStat:
    if isinstance(row, HitStat):
        return row
    data = dict(row or {})
    return HitStat(
        attempts=_int(data.get("attempts")),
        approved=_int(data.get("approved")),
        users=_int(data.get("users")),
        last_hit_at=parse_ts(data.get("last_hit_utc")),
    )


def _legacy_flags(
    row: Mapping[str, Any], fetched_at: datetime | None, now: datetime | None
) -> list[str]:
    flags: list[str] = []
    if _bool(row.get("prepaid")):
        flags.append("prepaid")
    if _bool(row.get("commercial")):
        flags.append("commercial")
    if not _bool(row.get("avs", 1)):
        flags.append("no AVS")
    days_old = _age_days(fetched_at, now)
    if days_old is not None and days_old >= 90:
        flags.append("stale data")
    if _int(_json(row.get("field_conf_json")).get("bank", 0)) < 60:
        flags.append("bank data uncertain")
    return flags


def from_legacy(
    record: Any,
    *,
    challenges: int = 0,
    last_state: str = "",
    hits: Mapping[str, Any] | HitStat | None = None,
    ttl_days: int = 0,
    now: datetime | None = None,
) -> BinRecord:
    """Map one legacy ``bins`` row (or ``BinRecord``) to the app model.

    ``ttl_days=0`` means the legacy store is permanent; the renderer prints
    ``permanent`` instead of inventing an expiry.
    """
    row = _row(record)
    bin8 = str(row.get("bin") or "").strip()
    sources = _json(row.get("sources_json"))
    source_names = {
        str(name)
        for provenance in sources.values()
        if isinstance(provenance, dict)
        for name in provenance
        if name
    }
    conflicts = sum(
        1
        for provenance in sources.values()
        if isinstance(provenance, dict) and len({str(value) for value in provenance.values()}) > 1
    )
    fetched_at = parse_ts(row.get("verified_utc"))
    site = str(row.get("site") or "").strip()
    site = site.replace("http://", "").replace("https://", "").rstrip("/")
    type_value = _clean(row.get("card_type"))
    return BinRecord(
        bin=bin8,
        parent6=bin8[:6],
        brand=_clean(row.get("brand")),
        type=type_value,
        tier=_clean(row.get("level")),
        prepaid=_bool(row.get("prepaid")),
        commercial=_bool(row.get("commercial")),
        debit=_bool(row.get("atm_debit")) if row.get("atm_debit") is not None else None,
        bank_short=_clean(row.get("bank")),
        bank_legal=None,
        bank_phone=_clean(row.get("phone")),
        bank_site=site or None,
        country=_clean(row.get("country")),
        iso2=str(row.get("iso2") or "").upper() or None,
        currency=_clean(row.get("currency")),
        pan_len=_int(row.get("card_length"), 16) or None,
        range_lo=None,
        range_hi=None,
        conflicts=conflicts,
        flags=_legacy_flags(row, fetched_at, now),
        sources_n=len(source_names) or (1 if bin8 else 0),
        fetched_at=fetched_at,
        ttl_days=max(0, _int(ttl_days)),
        r3ds=three_ds(row, challenges=challenges, last_state=last_state),
        hits=hit_stat(hits),
        sources=field_sources(row, fetched_at),
    )


def confidence(record: BinRecord, now: datetime | None = None) -> tuple[int, str, str]:
    """Section 17 score: BASE 45, green at 85, capped at 84 under 3 reads."""
    return app_confidence.score(record, now)


def gates_from_rows(
    rows: list[Mapping[str, Any]] | None, now: datetime | None = None
) -> list[GateView]:
    """Map legacy ``gates`` rows to the renderer's ``GateView``.

    The legacy table has no per-gate price, lane or country list. A check costs
    one credit in this bot, so that is the price shown; the only legacy gate
    (Stripe) handles 3DS.
    """
    current = now or datetime.now(UTC)
    out: list[GateView] = []
    for raw in rows or []:
        row = dict(raw)
        name = str(row.get("name") or "gate")
        kind = str(row.get("kind") or name)
        until = parse_ts(row.get("breaker_until"))
        state = "OPEN" if until is not None and until > current else "HEALTHY"
        label = "Stripe" if name.lower() == "stripe" else name.replace("_", " ").title()
        out.append(
            GateView(
                id=name,
                label=label,
                lane="auth",
                processor=kind,
                countries=("*",),
                cost_cr=Decimal("1"),
                amount_cents=0,
                ladder=(),
                state=state,
                p50_ms=None,
                p95_ms=None,
                success_pct=None,
                enabled=bool(row.get("enabled")),
                handles_3ds=kind.lower() in ("stripe", "adyen", "authnet"),
                needs_postal=False,
                key_country="",
                blurb="",
                sort_hint=0,
            )
        )
    return out


async def load(
    db: Any,
    record: Any,
    *,
    ttl_days: int = 0,
    now: datetime | None = None,
) -> BinRecord:
    """Build the render model with the evidence stored in ``checks``/``bin_hits``."""
    row = _row(record)
    bin8 = str(row.get("bin") or "").strip()
    challenges = await db.twofa_challenges(bin8) if bin8 else 0
    last_state = await db.last_twofa_state(bin8) if bin8 else ""
    hits = await db.get_bin_hits(bin8) if bin8 else None
    return from_legacy(
        row,
        challenges=challenges,
        last_state=last_state,
        hits=hits,
        ttl_days=ttl_days,
        now=now,
    )
