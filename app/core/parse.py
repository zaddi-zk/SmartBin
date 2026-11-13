"""Card, amount and money parsing (section 24 parser contract).

Accepts every separator a paste can contain — ``|``, space, ``/``, ``:``,
``;``, ``,`` and arbitrary whitespace — 2- or 4-digit years, PANs pasted in
4-digit groups, and an optional trailing postal code.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

_SEPARATORS = re.compile(r"[|/:;,\s]+")
_NON_DIGITS = re.compile(r"\D+")
_EXPIRY = re.compile(r"^(?P<month>\d{1,2})[/-]?(?P<year>\d{2,4})$")


class CardError(ValueError):
    """Raised with a user-safe reason when a paste cannot be a card."""


def luhn_ok(number: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(number)):
        digit = ord(char) - 48
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Card:
    number: str
    exp_month: int
    exp_year: int
    cvv: str
    postal: str = ""
    holder: str = ""
    country: str = ""

    @property
    def bin8(self) -> str:
        return self.number[:8]

    @property
    def first6(self) -> str:
        return self.number[:6]

    @property
    def last4(self) -> str:
        return self.number[-4:]

    @property
    def masked(self) -> str:
        return mask_pan(self.number)

    @property
    def pan_hash(self) -> str:
        return pan_hash(self.number)

    @classmethod
    def parse(cls, raw: str) -> Card:
        """Parse a single card line. Raises :class:`CardError` on bad input."""
        text = str(raw or "").strip()
        if not text:
            raise CardError("card is empty")

        cleaned = _SEPARATORS.sub(" ", text).strip()
        parts = cleaned.split(" ")

        # Join PANs pasted as groups: "4242 4242 4242 4242 11/29 716".
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
            raise CardError("expected cc|mm|yy|cvv")

        number = _NON_DIGITS.sub("", parts[0])
        if not 13 <= len(number) <= 19:
            raise CardError("card number length")
        if not luhn_ok(number):
            raise CardError("luhn")

        month, year = _parse_expiry(parts[1], parts[2])
        if not 1 <= month <= 12:
            raise CardError("expiry month")
        now = _now()
        if (year, month) < (now.year, now.month):
            raise CardError("expired")
        if year > now.year + 30:
            raise CardError("expiry year")

        cvv = parts[3]
        if not cvv.isdigit() or not 3 <= len(cvv) <= 4:
            raise CardError("cvv")

        postal = ""
        if len(parts) >= 5 and parts[4]:
            candidate = parts[4]
            if candidate.isdigit() or re.fullmatch(r"[A-Za-z0-9-]{2,10}", candidate):
                postal = candidate

        return cls(
            number=number,
            exp_month=month,
            exp_year=year,
            cvv=cvv,
            postal=postal,
        )

    @property
    def expiry_display(self) -> str:
        return f"{self.exp_month:02d}/{self.exp_year % 100:02d}"


def _parse_expiry(raw_month: str, raw_year: str) -> tuple[int, int]:
    joined = f"{raw_month}/{raw_year}"
    match = _EXPIRY.match(joined.replace(" ", ""))
    if not match:
        raise CardError("expiry")
    month = int(match.group("month"))
    year = int(match.group("year"))
    if year < 100:
        year += 2000
    return month, year


def mask_pan(number: str) -> str:
    """4465…9283 — the only form of a PAN that may reach a screen or a log."""
    digits = _NON_DIGITS.sub("", number)
    if len(digits) <= 8:
        return "…"
    return f"{digits[:4]}…{digits[-4:]}"


def pan_hash(number: str, pepper: str = "") -> str:
    digits = _NON_DIGITS.sub("", number)
    return hashlib.sha256(f"{pepper}{digits}".encode()).hexdigest()


def parse_amount_to_minor(raw: str | int | Decimal, *, allow_zero: bool = True) -> int:
    """Section 3.9: amounts are integers in minor units, never floats."""
    if isinstance(raw, int):
        value = Decimal(raw)
    else:
        try:
            value = Decimal(str(raw).strip().replace(",", "."))
        except (InvalidOperation, ValueError) as exc:
            raise CardError("amount") from exc
    if value < 0:
        raise CardError("amount")
    minor = value * 100
    if minor != minor.to_integral_value():
        raise CardError("amount has sub-cent precision")
    amount = int(minor)
    if amount == 0 and not allow_zero:
        raise CardError("amount must be positive")
    return amount


def fmt_minor(amount_cents: int, currency: str = "USD") -> str:
    symbol = "$" if currency.upper() == "USD" else ""
    whole, cents = divmod(abs(amount_cents), 100)
    body = f"{whole}.{cents:02d}" if cents else str(whole)
    return f"{symbol}{body}"


def fmt_cr(value: Decimal | int | float | str) -> str:
    """Render credits without a trailing ``.0`` and without float noise."""
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    text = format(decimal.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
