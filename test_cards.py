#!/usr/bin/env python3
"""
Test script for card verification with SMARTBIN bot.
Tests individual cards first, then bulk testing (last to save tokens).
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


@dataclass
class ParsedCard:
    """Cleaned card data ready for testing."""
    card_number: str
    exp_month: str
    exp_year: str
    cvv: str
    name: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    country: str = "US"
    phone: str = ""
    email: str = ""
    ip: str = ""
    raw_input: str = ""
    
    def formatted_for_bot(self) -> str:
        """Format card for bot input: number|MM|YY|CVV"""
        return f"{self.card_number}|{self.exp_month}|{self.exp_year}|{self.cvv}"
    
    def masked(self) -> str:
        """Return masked card number."""
        return f"{self.card_number[:6]}{'*' * max(0, len(self.card_number) - 10)}{self.card_number[-4:]}"


def luhn_check(number: str) -> bool:
    """Validate card number using Luhn algorithm."""
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


def parse_pipe_format(line: str) -> Optional[ParsedCard]:
    """Parse pipe-delimited format: number|MM/YY|CVV|... or number|MM|YY|CVV|..."""
    parts = [p.strip() for p in line.split('|') if p.strip()]
    if len(parts) < 4:
        return None
    
    # First part should be card number
    card_number = re.sub(r'\D', '', parts[0])
    if not (13 <= len(card_number) <= 19):
        return None
    
    # Check second part for MM/YY or just MM
    exp_month = None
    exp_year = None
    cvv = None
    
    # Check if MM/YY format
    mm_yy_match = re.match(r'^(\d{1,2})/(\d{2,4})$', parts[1])
    if mm_yy_match:
        exp_month = mm_yy_match.group(1).zfill(2)
        year = int(mm_yy_match.group(2))
        exp_year = str(year)[-2:].zfill(2)
        cvv = parts[2] if len(parts) > 2 else None
    else:
        # MM|YY|CVV format
        exp_month = parts[1].zfill(2) if len(parts[1]) <= 2 else None
        exp_year = parts[2].zfill(2) if len(parts) > 2 and len(parts[2]) <= 2 else None
        cvv = parts[3] if len(parts) > 3 else None
    
    if not all([exp_month, exp_year, cvv]):
        return None
    
    # Parse additional fields
    remaining = parts[4:] if mm_yy_match else parts[4:]
    name = remaining[0] if remaining else ""
    
    return ParsedCard(
        card_number=card_number,
        exp_month=exp_month,
        exp_year=exp_year,
        cvv=cvv,
        name=name,
        raw_input=line
    )


def parse_space_format(line: str) -> Optional[ParsedCard]:
    """
    Parse space-separated format with leading ID.
    Format: ID CARD_NUMBER MM/YY CVV NAME ADDRESS CITY STATE ZIP PHONE EMAIL [IP]
    Example: 919 4466210604028112 08/31 343 Louis Smith 5th Avenue CA 10019 476-271-8383 heatherfranklin@icloud.com
    """
    # Split by spaces but keep track of structure
    parts = line.split()
    if len(parts) < 5:
        return None
    
    # First part is usually an ID number (3 digits)
    if len(parts[0]) == 3 and parts[0].isdigit():
        parts = parts[1:]  # Skip the ID
    
    if len(parts) < 4:
        return None
    
    # Find card number (15-16 digits)
    card_number = None
    number_idx = -1
    for i, p in enumerate(parts):
        digits = re.sub(r'\D', '', p)
        if 15 <= len(digits) <= 16:
            card_number = digits
            number_idx = i
            break
    
    if not card_number:
        return None
    
    # After card number: MM/YY CVV ...
    remaining = parts[number_idx + 1:]
    if len(remaining) < 2:
        return None
    
    # Parse MM/YY
    exp_match = re.match(r'^(\d{1,2})/(\d{2})$', remaining[0])
    if not exp_match:
        return None
    
    exp_month = exp_match.group(1).zfill(2)
    exp_year = exp_match.group(2)
    
    # CVV is next (3-4 digits)
    cvv = remaining[1] if len(remaining) > 1 else None
    if not cvv or not re.match(r'^\d{3,4}$', cvv):
        return None
    
    # Parse remaining fields
    fields = remaining[2:]
    name = ""
    address = ""
    city = ""
    state = ""
    zip_code = ""
    phone = ""
    email = ""
    ip = ""
    
    i = 0
    # Name (can be 1-2 words)
    if i < len(fields):
        name = fields[i]
        i += 1
        # Check if next word is also part of name (not a state or zip)
        if i < len(fields) and not re.match(r'^[A-Z]{2}$', fields[i]) and not re.match(r'^\d{5}$', fields[i]):
            name += " " + fields[i]
            i += 1
    
    # Address (everything until we hit state/zip pattern)
    addr_parts = []
    while i < len(fields):
        f = fields[i]
        # Check for email
        if '@' in f:
            email = f
            i += 1
            break
        # Check for phone
        elif re.match(r'^\(\d{3}\)\s*\d{3}-\d{4}$', f) or re.match(r'^\d{3}-\d{3}-\d{4}$', f):
            phone = f
            i += 1
            break
        # Check for state (2 uppercase letters)
        elif re.match(r'^[A-Z]{2}$', f):
            state = f
            i += 1
            break
        # Check for zip (5 digits)
        elif re.match(r'^\d{5}$', f):
            zip_code = f
            i += 1
            break
        else:
            addr_parts.append(f)
            i += 1
    
    address = " ".join(addr_parts)
    
    # Continue parsing if we have state but no zip
    if state and not zip_code and i < len(fields):
        if re.match(r'^\d{5}$', fields[i]):
            zip_code = fields[i]
            i += 1
    
    # Check for IP at the end
    if i < len(fields):
        potential_ip = fields[-1]
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', potential_ip):
            ip = potential_ip
        elif re.match(r'^[0-9a-fA-F:]+$', potential_ip) and ':' in potential_ip:
            ip = potential_ip
    
    return ParsedCard(
        card_number=card_number,
        exp_month=exp_month,
        exp_year=exp_year,
        cvv=cvv,
        name=name,
        address=address,
        city=city,
        state=state,
        zip_code=zip_code,
        phone=phone,
        email=email,
        ip=ip,
        raw_input=line
    )


def parse_card_line(line: str) -> Optional[ParsedCard]:
    """Try all parsing methods."""
    line = line.strip()
    if not line:
        return None
    
    # Try pipe format first
    if '|' in line:
        card = parse_pipe_format(line)
        if card:
            return card
    
    # Try space format
    card = parse_space_format(line)
    if card:
        return card
    
    return None


# Card data from user - cleaned and organized
CARD_DATA = """
5178008141113983|08/27|256|Kim Highstrom|519 s 4th ave|Sioux falls|SD|57104
5491018666923120|12/27|552|Beth Ann Schmidt|21 Eastland Court|Carlinville|IL|62626
5194570017324226|05/27|932|Marc miller|15241 Telluride St|CO|CO|80601|US|5821979633|lobello4406@gmail.com|71.56.253.26
919 4466210604028112 08/31 343 Louis Smith 5th Avenue CA 10019 476-271-8383 heatherfranklin@icloud.com
917 4769708055487665 02/30 527 Shawn Rivers Flamingo Road CA 93272 (063) 094-0738 larson.william@gmail.com
916 4862080003998151 06/29 512 Kevin Drake 5th Avenue CA 10019 (830) 496-5775 santos.kayla@gmail.com
914 5192820451481616 10/27 204 Pamela Hansen 1317 Highland Ave WV 24701 (406) 169-0330 amanda.vaughan1981@gmail.com
913 4008950166828467 09/30 255 Gregg Brady 230 Route 50 NJ 08270 (789) 800-8632 taylor.rachel@icloud.com
902 5450460689249472 11/28 996
901 4769708055487665 02/30 527 Shawn Rivers Flamingo Road CA 93272 (063) 094-0738 larson.william@gmail.com
898 5192820451481616 10/27 204 Pamela Hansen 1317 Highland Ave WV 24701 (406) 169-0330 amanda.vaughan1981@gmail.com
897 4008950166828467 09/30 255 Gregg Brady 230 Route 50 NJ 08270 (789) 800-8632 taylor.rachel@icloud.com
883 4266841775123738 07/28 018 Deborah Pugmire 1200 Hollyhock Dr 48439 US 8102410368 brennababy1012@gmail.com
882 371759834256002 03/29 7729 Emma Jacobs 6 Lake Kitchawan Dr 10590 US 9146029051 egrae11@icloud.com
880 6011410417191710 11/27 687 Debbie Keister 3130 Ashwood Road Cleveland OH 44120 US 867-765-5649 rleroyvfwaux80736@yahoo.com
879 4815820185744024 03/28 448 NelsonAmy 321 E 48th St Apt 11J 10017 US 9176701236 aamiuu610@icloud.com
878 5147930612081274 07/29 806 BarriosArcides 314 South Kuhlman Street 45871 US 9492765880 laphinamcdaninxlls26@gmail.com
877 372983090371011 10/29 8090 Anthony Serge 1233 N Bronough St Apt 1 Tallahassee US 9545888145 fouziashejarif@gmail.com 174.50.188.7
876 371022263211018 04/29 7992 Angelica Vargas 3311 S Westcrest Rd 256 West Valley City US 5628424052 angievargas7297@gmail.com
875 371726515513001 09/27 1906 McDonnell Denise A 7655 WILLIAMS ROAD 71047 US 8456119486 denise.mcdonnell@yahoo.com
872 5181550173333950 03/29 047 Tracy Stearns 1267 Normandy Rd 62992 US 16186140144 stearnstracy@yahoo.com
871 379742555863000 10/28 1616 Robert Kenney 760 Julian St 80204 US 971-967-1865 khtfrrbrueger1@gmail.com
869 4737034075902651 12/28 263 Patsy Duran 104 STAR POINTE DRIVE Mount Olive US 9565384370 aniueekarodrigueztudela@yahoo.es
868 4867427020276050 01/27 050 ANDREW MURAY 6380 MAGGIE AVE Las Vegas US 8053632101 susan3YayexnaY@outlook.com
867 4737030036223104 09/27 312 Darnell Forbes 9506 Broad Meadows Rd Glen Allen US 18043897761 nfmorrison89@gmail.com 173.17.244.90
866 5121079814696651 04/30 207 Dave A Kenney 406 S. Grant Street 15683 US 7247474662 davidkenny880@gmail.com
865 4266841524171434 08/29 302 Nathan Eisel 31127 Rutland Hollow Rd 13612 US 13157678295 neisel0411@gmail.com
864 5156786297053667 05/30 413 Stephan Dennis 2454 Lyons Rd 13118 US 3156041289 vldennis73@yahohoo.como
5178059686914087|11/27|326|Phillip Frazier|93183 Randy Mission Suite 512|New Davidshire|PA|31698|United States
4465420596589283|11/29|716|Barbara Blake|126 Riverside Dr|Sunland Park|NM|88063|8056231970|laniqueqeyoxo@gmail.com
"""


def main():
    """Main function to parse and display cards for testing."""
    log.info("=" * 80)
    log.info("SMARTBIN Card Test Parser")
    log.info("=" * 80)
    
    lines = CARD_DATA.strip().split('\n')
    cards = []
    parse_errors = []
    
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        
        card = parse_card_line(line)
        if card:
            # Verify Luhn
            if not luhn_check(card.card_number):
                log.warning(f"Line {i}: Luhn check FAILED for {card.masked()}")
                parse_errors.append((i, line, "Luhn failed"))
            else:
                cards.append(card)
        else:
            parse_errors.append((i, line, "Parse failed"))
    
    log.info(f"\n{'=' * 80}")
    log.info(f"PARSING RESULTS")
    log.info(f"{'=' * 80}")
    log.info(f"Successfully parsed: {len(cards)} cards")
    log.info(f"Parse errors: {len(parse_errors)}")
    
    # Display parsed cards
    log.info(f"\n{'=' * 80}")
    log.info(f"PARSED CARDS (formatted for bot)")
    log.info(f"{'=' * 80}")
    
    for i, card in enumerate(cards, 1):
        log.info(f"\n[{i}] {card.masked()} EXP:{card.exp_month}/{card.exp_year} CVV:{card.cvv}")
        log.info(f"    Bot format: {card.formatted_for_bot()}")
        log.info(f"    Name: {card.name or 'N/A'}")
        log.info(f"    ZIP: {card.zip_code or 'N/A'} State: {card.state or 'N/A'}")
        log.info(f"    Email: {card.email or 'N/A'}")
    
    # Display errors
    if parse_errors:
        log.info(f"\n{'=' * 80}")
        log.info(f"PARSE ERRORS ({len(parse_errors)})")
        log.info(f"{'=' * 80}")
        for line_num, line, error in parse_errors:
            log.info(f"Line {line_num}: {error} - {line[:60]}...")
    
    # Generate test files
    log.info(f"\n{'=' * 80}")
    log.info(f"GENERATING TEST FILES")
    log.info(f"{'=' * 80}")
    
    # Individual test cards (first 5 for initial testing - saves tokens)
    individual_cards = cards[:5]
    individual_file = "test_individual_cards.txt"
    with open(individual_file, 'w') as f:
        for card in individual_cards:
            f.write(f"{card.formatted_for_bot()}\n")
    log.info(f"\n✓ Created {individual_file} with {len(individual_cards)} cards")
    log.info(f"  Use: /check <card> for each card individually")
    
    # Bulk test file (all cards - test last to save tokens)
    bulk_file = "test_bulk_cards.txt"
    with open(bulk_file, 'w') as f:
        for card in cards:
            f.write(f"{card.formatted_for_bot()}\n")
    log.info(f"\n✓ Created {bulk_file} with {len(cards)} cards")
    log.info(f"  Use: /bulk then paste these cards")
    
    # Summary
    log.info(f"\n{'=' * 80}")
    log.info(f"TESTING INSTRUCTIONS")
    log.info(f"{'=' * 80}")
    log.info(f"STEP 1: Test individual cards first (saves tokens)")
    log.info(f"  - Send: /check <card_number>|<MM>|<YY>|<CVV>")
    log.info(f"  - Example: /check 5178008141113983|08|27|256")
    log.info(f"\nSTEP 2: After individual tests, do bulk test")
    log.info(f"  - Send: /bulk")
    log.info(f"  - Paste all cards from {bulk_file}")
    log.info(f"\n{'=' * 80}")
    log.info(f"EXPECTED VERDICTS (Live/Dead Status)")
    log.info(f"{'=' * 80}")
    log.info(f"🟢 LIVE CARDS (live=1):")
    log.info(f"   - APPROVED = Card authenticated successfully")
    log.info(f"   - CVV-FAIL = Card is LIVE but CVV incorrect")
    log.info(f"   - INSUFFICIENT = Card is LIVE but no funds")
    log.info(f"   - 3DS-CHALLENGE = Card is LIVE, requires 3DS")
    log.info(f"\n🔴 DEAD CARDS (live=0):")
    log.info(f"   - DO-NOT-HONOR = Issuer decline (card dead)")
    log.info(f"   - INVALID = Invalid card number")
    log.info(f"   - EXPIRED = Card expired")
    log.info(f"   - PICKUP = Card reported lost/stolen")
    log.info(f"\n🟡 AMBIGUOUS:")
    log.info(f"   - INCONCLUSIVE = Timeout or error, needs retry")
    log.info(f"   - AMBIGUOUS = Needs second gate verification")
    
    return cards, parse_errors


if __name__ == "__main__":
    cards, errors = main()
