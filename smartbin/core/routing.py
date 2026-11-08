from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from core.twofa import is_eea


def route(record: Mapping[str, Any], card: Any) -> dict[str, Any]:
    """Keys in order: issuer ISO2 -> brand -> level -> prepaid flag."""
    iso2 = str(record.get("iso2") or "").upper()
    brand = str(record.get("brand") or "UNKNOWN")
    level = str(record.get("level") or "UNKNOWN")
    prepaid = bool(record.get("prepaid"))
    eea = is_eea(iso2)
    note = (
        f"route iso2={iso2 or 'UNKNOWN'} brand={brand} level={level} "
        f"prepaid={int(prepaid)} eea={int(eea)}"
    )
    return {
        "iso2": iso2,
        "brand": brand,
        "level": level,
        "prepaid": prepaid,
        "eea": eea,
        "note": note,
    }


def _breaker_open(row: Mapping[str, Any], now: datetime) -> bool:
    raw = str(row.get("breaker_until") or "")
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return until > now
    except ValueError:
        return False


def choose_gate(
    gates: list[Mapping[str, Any]],
    routing: Mapping[str, Any],
    purpose: str = "check",
) -> Mapping[str, Any] | None:
    """Prepaid never hits strict-AVS gates. EEA enrollment never uses the own-Stripe gate."""
    now = datetime.now(timezone.utc)
    candidates: list[Mapping[str, Any]] = []
    for raw_row in gates:
        row = dict(raw_row)
        if not row.get("enabled"):
            continue
        if _breaker_open(row, now):
            continue
        kind = str(row.get("kind") or "")
        name = str(row.get("name") or "")
        if routing.get("prepaid") and ("avs" in kind.lower() or "avs" in name.lower() or kind == "strict"):
            continue
        if purpose == "enroll" and routing.get("eea") and name in ("stripe", "own-stripe"):
            continue
        candidates.append(row)
    if not candidates:
        return None
    candidates.sort(key=lambda r: (str(r.get("kind") or "") != "stripe", str(r.get("name") or "")))
    return candidates[0]


def amount_for(routing: Mapping[str, Any], purpose: str, min_minor: int = 3100) -> int:
    if purpose == "enroll":
        return min_minor if routing.get("eea") else 50
    return 100 if routing.get("eea") else 50


def routing_note(routing: Mapping[str, Any], gate_name: str, purpose: str) -> str:
    return f"{routing.get('note', '')} gate={gate_name} purpose={purpose}"
