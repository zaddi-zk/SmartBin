#!/usr/bin/env python3
"""Card Normalizer v2 — fullz parser for SMARTBIN v7.1.0

Parses messy card/fullz pastes into canonical records.
Accepts: pipe, comma, semicolon, multi-space delimiters.
Handles: junk counters, hex tokens, ZIP+4, IPv6, Amex 15-digit, leading-zero CVV.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from typing import Any


def luhn_ok(number: str) -> bool:
    """Validate Luhn checksum."""
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
class CanonicalCard:
    """Canonical card record (internal dict, never render raw)."""
    pan: str = ""
    mm: str = ""
    yyyy: str = ""
    cvv: str = ""
    name: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    zip4: str = ""
    phone: str = ""
    email: str = ""
    ip: str = ""
    pan_len: int = 0

    def pan_hash(self) -> str:
        """HMAC-style hash of PAN for dedupe."""
        return hashlib.sha256(self.pan.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pan": self.pan,
            "mm": self.mm,
            "yyyy": self.yyyy,
            "cvv": self.cvv,
            "name": self.name,
            "street": self.street,
            "city": self.city,
            "state": self.state,
            "zip": self.zip,
            "zip4": self.zip4,
            "phone": self.phone,
            "email": self.email,
            "ip": self.ip,
            "pan_len": self.pan_len,
            "pan_hash": self.pan_hash(),
        }


def _is_hex_token(s: str) -> bool:
    """Detect hex/garbage tokens like 6aca26e406625."""
    if len(s) < 8:
        return False
    # Must contain at least one letter (a-f) to be considered hex
    # Pure numeric strings (like phone numbers) are not hex tokens
    if not re.search(r"[a-fA-F]", s):
        return False
    return bool(re.fullmatch(r"[0-9a-fA-F]{8,}", s))


def _is_ipv4(s: str) -> bool:
    """Check if string is IPv4."""
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _is_ipv6(s: str) -> bool:
    """Check if string is IPv6."""
    return bool(re.fullmatch(r"([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}", s))


def _normalize_phone(s: str) -> str:
    """Normalize phone to digits."""
    return re.sub(r"\D", "", s)


def _looks_like_street_token(token: str) -> bool:
    """Check if token looks like part of a street address."""
    token_upper = token.upper()

    # Street suffixes - but be careful with "St" which is very common
    street_suffixes = ["Street", "Ave", "Avenue", "Rd", "Road", "Dr", "Drive",
                       "Blvd", "Boulevard", "Ln", "Lane", "Ct", "Court", "Way",
                       "Pl", "Place", "Ter", "Terrace", "Cir", "Circle"]
    # Check for full street suffix (case insensitive)
    for suffix in street_suffixes:
        if token_upper.endswith(suffix.upper()):
            return True

    # "St" alone is ambiguous - only treat as street if preceded by a number or street name
    if token_upper == "ST":
        return True

    # Check for address number prefix (e.g., "15241")
    if re.match(r"^\d+$", token):
        return True

    # Check for Apt, Suite, Unit, etc.
    if re.match(r"^(Apt|Suite|Ste|Unit|PO Box|#)\d*$", token, re.IGNORECASE):
        return True

    return False


def parse_line(line: str) -> CanonicalCard | None:
    """Parse a single fullz line into canonical record."""
    line = line.strip()
    if not line:
        return None

    # First, normalize delimiters: replace | ; with space, then split
    normalized = re.sub(r"[|;]+", " ", line)
    normalized = re.sub(r",\s*", " ", normalized)
    parts = normalized.split()

    if not parts:
        return None

    card = CanonicalCard()

    # Skip leading junk counters (e.g., "919", "877", "901")
    idx = 0
    while idx < len(parts) and parts[idx].isdigit() and len(parts[idx]) <= 4:
        idx += 1

    # Find PAN: first run of 13-19 digits that passes Luhn
    pan_found = False
    for i in range(idx, len(parts)):
        token = parts[i]
        digits = re.sub(r"\D", "", token)
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            card.pan = digits
            card.pan_len = len(digits)
            idx = i + 1
            pan_found = True
            break

    if not pan_found:
        return None

    # Find expiry: look for MM (01-12) followed by YY or YYYY
    expiry_found = False

    # Try Pattern 1 first: MM/YY or MM/YYYY
    expiry_pattern = re.compile(r"(\d{1,2})[/\-](\d{2,4})")
    for i in range(idx, len(parts)):
        match = expiry_pattern.search(parts[i])
        if match:
            mm = match.group(1).zfill(2)
            yy = match.group(2)
            if len(yy) == 2:
                yyyy = "20" + yy if int(yy) > 50 else "21" + yy
            else:
                yyyy = yy
            if 1 <= int(mm) <= 12:
                card.mm = mm
                card.yyyy = yyyy
                expiry_found = True
                break

    # Try Pattern 2: MM then YY/YYYY in adjacent tokens (pipe-delimited format)
    if not expiry_found:
        for i in range(idx, len(parts) - 1):
            token = parts[i]
            next_token = parts[i + 1]

            if re.fullmatch(r"\d{1,2}", token):
                mm = int(token)
                if 1 <= mm <= 12:
                    if re.fullmatch(r"\d{2,4}", next_token):
                        yy = next_token
                        if len(yy) == 2:
                            yyyy = "20" + yy if int(yy) > 50 else "21" + yy
                        else:
                            yyyy = yy
                        card.mm = str(mm).zfill(2)
                        card.yyyy = yyyy
                        expiry_found = True
                        idx = i + 2
                        break

    if not expiry_found:
        return None

    # Find CVV: 3-4 digits after PAN/expiry (keep leading zeros)
    for i in range(idx, min(idx + 5, len(parts))):
        token = parts[i]
        if re.fullmatch(r"\d{3,4}", token):
            if not token.startswith("20"):
                card.cvv = token
                idx = i + 1
                break

    # Parse remaining fields - work backwards from end for ZIP/state
    remaining = parts[idx:]

    # First pass: identify fixed fields (IP, email, phone, state)
    # ZIP is handled in second pass to avoid capturing address numbers
    known_indices = set()
    state_idx = None  # Track state index for city detection

    for i, token in enumerate(remaining):
        # Skip hex tokens
        if _is_hex_token(token):
            known_indices.add(i)
            continue

        # Skip US country token
        if token.upper() == "US":
            known_indices.add(i)
            continue

        # IP detection (v4 or v6)
        if _is_ipv4(token) or _is_ipv6(token):
            if not card.ip:
                card.ip = token
            known_indices.add(i)
            continue

        # Email detection
        if "@" in token and "." in token:
            card.email = token
            known_indices.add(i)
            continue

        # Phone detection (various formats) - at least 10 digits
        # Check BEFORE ZIP to avoid capturing phone as ZIP
        phone_digits = re.sub(r"\D", "", token)
        if len(phone_digits) >= 10:
            if not card.phone:
                card.phone = phone_digits
            known_indices.add(i)
            continue

        # ZIP+4 detection (12345-6789)
        zip_match = re.match(r"(\d{5})-(\d{4})", token)
        if zip_match:
            card.zip = zip_match.group(1)
            card.zip4 = zip_match.group(2)
            known_indices.add(i)
            continue

        # ZIP detection (5 digits) - only if no phone detected yet
        # This helps distinguish between phone and ZIP
        if re.fullmatch(r"\d{5}", token) and not card.zip:
            card.zip = token
            known_indices.add(i)
            continue
        # Check for phone part patterns: (XXX) or XXX-XXX
        if re.match(r"^\(\d{3}\)$", token) or re.match(r"^\d{3}-\d{3}$", token):
            if not card.phone:
                # Try to combine with next token
                if i + 1 < len(remaining):
                    next_token = remaining[i + 1]
                    next_digits = re.sub(r"\D", "", next_token)
                    if len(phone_digits + next_digits) >= 10:
                        card.phone = phone_digits + next_digits
                        known_indices.add(i + 1)
            known_indices.add(i)
            continue

        # State detection (2-letter uppercase)
        if re.fullmatch(r"[A-Z]{2}", token):
            if not card.state:
                card.state = token
                state_idx = i  # Track state index
            else:
                # Duplicate state (CO|CO) → city becomes UNKNOWN
                if card.state == token:
                    card.city = "UNKNOWN"
            known_indices.add(i)
            continue

    # Second pass: name, street, city from remaining tokens
    # Key insight: city is the token immediately BEFORE the state
    name_parts = []
    street_parts = []
    in_name = True
    name_cap_threshold = 2  # Only 2 tokens for name (first + last)
    found_street_start = False  # Track when we've started seeing street tokens

    for i, token in enumerate(remaining):
        if i in known_indices:
            continue

        # ZIP detection (5 digits) - only if we've seen state already AND it's not already known
        # This avoids capturing address numbers like "15241"
        if re.fullmatch(r"\d{5}", token) and not card.zip and card.state and i not in known_indices:
            card.zip = token
            known_indices.add(i)
            continue

        # Name: capitalized tokens at start (2 tokens max - first + last)
        if re.match(r"^(Mr|Mrs|Ms|Mx)?[A-Z][a-z]+", token):
            if in_name and len(name_parts) < name_cap_threshold:
                name_parts.append(token)
                continue
            in_name = False

        # The token immediately before state is the city
        if state_idx is not None and i == state_idx - 1:
            if re.match(r"[A-Z][a-z]+", token):
                card.city = token
                continue

        # Check if it's a street token (including address numbers, directional prefixes like N/S/E/W)
        if _looks_like_street_token(token) or re.fullmatch(r"[NSWE]", token):
            found_street_start = True
            street_parts.append(token)
            continue

        # Pure number tokens (like "15241") are part of street address
        # But 10+ digit numbers are phone numbers
        if re.fullmatch(r"\d+", token) and not in_name:
            if len(token) >= 10 and not card.phone:
                card.phone = token
                known_indices.add(i)
                continue
            found_street_start = True
            street_parts.append(token)
            continue

        # Capitalized token - treat as street name if we haven't found city yet
        if re.match(r"[A-Z][a-z]", token):
            if not card.city and (state_idx is None or i != state_idx - 1):
                street_parts.append(token)
            continue

        # Street: everything else
        street_parts.append(token)

    if name_parts:
        card.name = " ".join(name_parts)

    if street_parts:
        card.street = " ".join(street_parts)

    # Validate we have minimum fields
    if not card.pan or not card.mm or not card.yyyy:
        return None

    return card


def parse_file(filepath: str) -> tuple[list[CanonicalCard], int, int]:
    """Parse a file of card lines. Returns (cards, raw_count, dupe_count)."""
    cards = []
    seen_hashes = set()
    raw_count = 0
    dupe_count = 0

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            raw_count += 1
            card = parse_line(line)
            if card is None:
                continue

            phash = card.pan_hash()
            if phash in seen_hashes:
                dupe_count += 1
                continue

            seen_hashes.add(phash)
            cards.append(card)

    return cards, raw_count, dupe_count


def format_table(cards: list[CanonicalCard]) -> str:
    """Format cards as a fixed-width table."""
    lines = [
        "PAN (masked)       | MM/YY    | CVV | NAME              | CITY          | STATE | ZIP      | PHONE (last4) | EMAIL (masked)",
        "-" * 140,
    ]
    for card in cards:
        pan_masked = f"{card.pan[:6]}{'*' * max(0, len(card.pan) - 10)}{card.pan[-4:]}" if card.pan else "-"
        expiry = f"{card.mm}/{card.yyyy}" if card.mm and card.yyyy else "-"
        cvv = card.cvv if card.cvv else "-"
        name = (card.name or "-")[:17]
        city = (card.city or "-")[:13]
        state = card.state or "-"
        zip_code = f"{card.zip}" if card.zip else "-"
        if card.zip4:
            zip_code += f"+{card.zip4}"
        phone_last4 = f"***{card.phone[-4:]}" if card.phone else "-"
        email_masked = ""
        if card.email:
            at_idx = card.email.find("@")
            if at_idx > 0:
                local = card.email[:2] + "***"
                domain = card.email[at_idx:]
                email_masked = f"{local}{domain}"[:20]

        lines.append(
            f"{pan_masked:<18} | {expiry:<8} | {cvv:<3} | {name:<17} | {city:<13} | {state:<5} | {zip_code:<8} | {phone_last4:<13} | {email_masked}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Card Normalizer v2 — SMARTBIN v7.1.0")
    parser.add_argument("files", nargs="+", help="Input file(s) with card/fullz data")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--quiet", action="store_true", help="Suppress table output")

    args = parser.parse_args()

    all_cards: list[CanonicalCard] = []
    total_raw = 0
    total_dupes = 0

    for filepath in args.files:
        cards, raw, dupes = parse_file(filepath)
        all_cards.extend(cards)
        total_raw += raw
        total_dupes += dupes

    if args.json:
        import json

        output = {
            "raw_lines": total_raw,
            "unique_cards": len(all_cards),
            "duplicates": total_dupes,
            "cards": [c.to_dict() for c in all_cards],
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"Parsed {total_raw} raw lines → {len(all_cards)} unique ({total_dupes} dupes)")
        if not args.quiet and all_cards:
            print()
            print(format_table(all_cards))

    return 0


if __name__ == "__main__":
    sys.exit(main())
