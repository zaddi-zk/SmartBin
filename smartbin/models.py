from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_date() -> str:
    return utcnow().strftime("%Y-%m-%d")


def luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) < 12:
        return False
    total = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class Card:
    number: str
    bin8: str
    exp_month: int
    exp_year: int
    cvv: str

    @property
    def last4(self) -> str:
        return self.number[-4:]

    @property
    def first6(self) -> str:
        return self.number[:6]

    def masked(self) -> str:
        return f"{self.number[:6]}{'*' * max(0, len(self.number) - 10)}{self.last4}"

    @classmethod
    def parse(cls, raw: str) -> "Card":
        """Parse any common card paste format.

        Separators: | / : ; , or whitespace. Accepts 2- or 4-digit years,
        PANs pasted in 4-digit groups, and an optional trailing zip/fullz.
        """
        cleaned = re.sub(r"[|/:;,\s]+", " ", str(raw or "")).strip()
        if not cleaned:
            raise ValueError("card is empty")
        parts = cleaned.split(" ")
        # Re-join PANs pasted as groups: 4242 4242 4242 4242 11/29 716
        while (
            len(parts) > 4
            and parts[0].isdigit()
            and parts[1].isdigit()
            and len(parts[0]) <= 15
            and len(parts[1]) == 4
        ):
            parts[0] = parts[0] + parts[1]
            parts.pop(1)
        if len(parts) < 4:
            raise ValueError("card must be number MM YY CVV")
        number = re.sub(r"\D", "", parts[0])
        if not (13 <= len(number) <= 19):
            raise ValueError("card number length")
        if not luhn_ok(number):
            raise ValueError("luhn")
        try:
            month = int(parts[1])
            year = int(parts[2])
        except ValueError as exc:
            raise ValueError("expiry") from exc
        if year < 100:
            year += 2000
        if not 1 <= month <= 12:
            raise ValueError("expiry month")
        now = utcnow()
        if (year, month) < (now.year, now.month):
            raise ValueError("expired")
        cvv = parts[3]
        if not cvv.isdigit() or not 3 <= len(cvv) <= 4:
            raise ValueError("cvv")
        return cls(number=number, bin8=number[:8], exp_month=month, exp_year=year, cvv=cvv)


BIN_FIELDS = (
    "bin",
    "brand",
    "level",
    "bank",
    "phone",
    "site",
    "country",
    "iso2",
    "currency",
    "prepaid",
    "commercial",
    "bin_age",
    "card_length",
    "luhn",
    "dob",
    "ssn",
    "mmn",
    "avs",
    "sources_json",
    "field_conf_json",
    "confidence",
    "verified_utc",
    "sample_size",
    "corrections",
    "twofa",
    "twofa_conf",
    "twofa_reads",
    "last_twofa_utc",
    "amt_tested",
)


@dataclass
class BinRecord:
    bin: str = ""
    brand: str = "UNKNOWN"
    level: str = "UNKNOWN"
    bank: str = "UNKNOWN"
    phone: str = ""
    site: str = ""
    country: str = "UNKNOWN"
    iso2: str = ""
    currency: str = ""
    prepaid: int = 0
    commercial: int = 0
    bin_age: int = 0
    card_length: int = 16
    luhn: int = 1
    dob: int = 0
    ssn: int = 0
    mmn: int = 0
    avs: int = 1
    sources_json: str = "{}"
    field_conf_json: str = "{}"
    confidence: float = 0.0
    verified_utc: str = ""
    sample_size: int = 0
    corrections: int = 0
    twofa: str = "UNKNOWN"
    twofa_conf: float = 0.0
    twofa_reads: int = 0
    last_twofa_utc: str = ""
    amt_tested: int = 0

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | None) -> "BinRecord":
        if row is None:
            return cls()
        data = dict(row)
        return cls(**{k: data[k] for k in BIN_FIELDS if k in data})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_empty(self) -> bool:
        return not self.verified_utc and self.brand == "UNKNOWN" and self.bank == "UNKNOWN"


@dataclass
class CheckOutcome:
    verdict: str = "INCONCLUSIVE"
    live: int = 0
    action: str = ""
    raw_code: str = ""
    decline_code: str = ""
    twofa_read: str = ""
    amount_band: int = 0
    canary_ok: int = -1
    acs_host: str = ""
    latency_ms: int = 0
    proxy_id: int = 0
    note: str = ""
    retry: bool = False
    gate: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
