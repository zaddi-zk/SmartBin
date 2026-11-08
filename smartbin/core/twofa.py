from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

EEA_ISO2 = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE", "IS", "LI", "NO",
}

CONF = {"VBV": 95.0, "NON-VBV": 80.0, "NON-VBV*": 78.0, "LOW-CONF": 45.0, "PARTIAL": 55.0}


def is_eea(iso2: str | None) -> bool:
    return (iso2 or "").upper() in EEA_ISO2


@dataclass
class TwoFARead:
    kind: str  # challenge | nonvbv | timeout | soft_decline | captcha
    gate: str = "stripe"
    ts: str = ""
    amount_minor: int = 0
    canary_ok: bool = True
    acs_host: str = ""
    iso2: str = ""
    gate_kind: str = "stripe"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hours_between(a: str, b: str) -> float:
    da, db = _parse_ts(a), _parse_ts(b)
    if da is None or db is None:
        return 0.0
    return abs((da - db).total_seconds()) / 3600.0


def _band(amount_minor: int) -> int:
    return int(amount_minor) // 100


def _no_write(record: Mapping[str, Any], note: str, check_read: str = "", gate_penalty: bool = False) -> dict[str, Any]:
    return {
        "write": False,
        "twofa": record.get("twofa", "UNKNOWN"),
        "twofa_conf": float(record.get("twofa_conf", 0.0)),
        "twofa_reads": int(record.get("twofa_reads", 0)),
        "last_twofa_utc": record.get("last_twofa_utc", ""),
        "amt_tested": int(record.get("amt_tested", 0)),
        "note": note,
        "check_read": check_read,
        "gate_penalty": gate_penalty,
        "acs_host": "",
    }


def _write(
    record: Mapping[str, Any],
    value: str,
    read: TwoFARead,
    note: str,
    check_read: str | None = None,
) -> dict[str, Any]:
    return {
        "write": True,
        "twofa": value,
        "twofa_conf": CONF[value],
        "twofa_reads": int(record.get("twofa_reads", 0)) + 1,
        "last_twofa_utc": read.ts,
        "amt_tested": read.amount_minor,
        "note": note,
        "check_read": check_read or value,
        "gate_penalty": False,
        "acs_host": read.acs_host,
    }


def _history_kind(row: Mapping[str, Any]) -> str:
    row = dict(row)
    value = str(row.get("twofa_read") or "")
    if value == "VBV":
        return "challenge"
    if value in ("NON-VBV", "NON-VBV*", "LOW-CONF", "PARTIAL"):
        return "nonvbv"
    return ""


def _enrollment_history(
    history: list[Mapping[str, Any]], read: TwoFARead, min_minor: int
) -> list[dict[str, Any]]:
    eea = is_eea(read.iso2)
    out: list[dict[str, Any]] = []
    for raw in history:
        row = dict(raw)
        kind = _history_kind(row)
        if not kind:
            continue
        if int(row.get("canary_ok", -1)) != 1:
            continue
        amount = int(row.get("amount_band", 0))
        if eea and amount < min_minor:
            continue
        out.append(
            {
                "kind": kind,
                "gate": str(row.get("gate") or ""),
                "ts": str(row.get("created_utc") or ""),
                "amount": amount,
            }
        )
    return out


def _partial(read: TwoFARead, prior: list[dict[str, Any]], min_minor: int) -> bool:
    """R9: >=2 enrollment-grade reads, same amount band, disagreeing outcomes."""
    if is_eea(read.iso2) and read.amount_minor < min_minor:
        return False
    band = _band(read.amount_minor)
    reads = [r for r in prior if _band(r["amount"]) == band]
    if not reads:
        return False
    kinds = {r["kind"] for r in reads} | {read.kind}
    return "challenge" in kinds and "nonvbv" in kinds and len(reads) >= 1


def evaluate(
    record: Mapping[str, Any],
    read: TwoFARead,
    history: list[Mapping[str, Any]] | None = None,
    min_minor: int = 3100,
) -> dict[str, Any]:
    """Ten M1 rules, no exceptions. Returns what to write back and what the check taught."""
    history = list(history or [])
    current = str(record.get("twofa") or "UNKNOWN")
    eea = is_eea(read.iso2)
    eligible = not (eea and read.amount_minor < min_minor)

    # R2: ACS timeout never counts as a challenge and never writes.
    if read.kind == "timeout":
        return _no_write(record, "R2 ACS timeout: INCONCLUSIVE")

    # R3: soft decline with 3DS language but no URL/form is not a challenge.
    if read.kind == "soft_decline":
        return _no_write(record, "R3 soft decline without real ACS evidence")

    # R10: challenge form with no ACS host on a Stripe-class gate.
    if read.kind == "captcha":
        if read.gate_kind == "stripe":
            return _no_write(
                record,
                "R10 POSSIBLE-CAPTCHA: no ACS host, gate health only",
                check_read="POSSIBLE-CAPTCHA",
                gate_penalty=True,
            )
        return _no_write(record, "challenge form without ACS host")

    if read.kind == "challenge":
        if not read.acs_host:
            if read.gate_kind == "stripe":
                return _no_write(
                    record,
                    "R10 POSSIBLE-CAPTCHA: no ACS host, gate health only",
                    check_read="POSSIBLE-CAPTCHA",
                    gate_penalty=True,
                )
            return _no_write(record, "R3 challenge without ACS host")

        # R4: EEA enrollment reads only at >= 3100 minor units.
        if not eligible:
            return _no_write(
                record,
                "R4 EXEMPTION read below EEA enrollment amount",
                check_read="EXEMPTION",
            )

        # R1 + R8: a real challenge is a VBV read and downgrades NON-VBV immediately.
        if current in ("NON-VBV", "NON-VBV*"):
            note = f"R8 downgrade NON-VBV -> VBV at {read.ts} by gate={read.gate}"
        else:
            note = "R1 real ACS host captured -> VBV"
        return _write(record, "VBV", read, note)

    if read.kind == "nonvbv":
        # R5: only canary-clean reads count.
        if not read.canary_ok:
            return _no_write(
                record,
                "R5 UNVERIFIED: canary window not clean",
                check_read="UNVERIFIED",
            )

        # R4: EEA reads below the enrollment amount are exemptions, never enrollment reads.
        if not eligible:
            return _no_write(
                record,
                "R4 EXEMPTION read below EEA enrollment amount",
                check_read="EXEMPTION",
            )

        prior = _enrollment_history(history, read, min_minor)

        # R9: PARTIAL needs >=2 enrollment-grade reads at one band with disagreement.
        if _partial(read, prior, min_minor):
            return _write(record, "PARTIAL", read, "R9 disagreeing enrollment reads at one band")

        if current in ("NON-VBV", "NON-VBV*"):
            gates = {r["gate"] for r in prior} | {read.gate}
            if len(gates) >= 2 and current == "NON-VBV*":
                return _write(record, "NON-VBV", read, "R6 two-gate read -> NON-VBV")
            return _write(record, current, read, "measured read corroborates stored 2FA")

        if current == "VBV":
            return _write(
                record,
                "LOW-CONF",
                read,
                "R9 insufficient for PARTIAL; stored VBV disagrees",
            )

        if current == "PARTIAL":
            return _write(record, "PARTIAL", read, "R9 PARTIAL retained")

        # R7: exactly one read ever stays LOW-CONF.
        if not prior:
            return _write(record, "LOW-CONF", read, "R7 single read: LOW-CONF")

        gates = {r["gate"] for r in prior} | {read.gate}
        if len(gates) >= 2:
            return _write(record, "NON-VBV", read, "R6 two-gate reads -> NON-VBV")

        if any(
            r["gate"] == read.gate and _hours_between(r["ts"], read.ts) >= 6.0
            for r in prior
        ):
            return _write(record, "NON-VBV*", read, "R6 two same-gate reads >=6h apart -> NON-VBV*")

        return _write(
            record,
            "LOW-CONF",
            read,
            "R6 same-gate reads <6h apart: not promoted",
        )

    return _no_write(record, "unknown 2FA read")
