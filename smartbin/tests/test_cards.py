"""Card Normalizer tests for SMARTBIN v7.1.0

Tests the 28 raw → 25 unique parser with all edge cases.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.normalize_cards import parse_line, parse_file, CanonicalCard


def test_raw_to_unique() -> None:
    """28 raw lines → 25 unique (3 dupes flagged)."""
    fixture = Path(__file__).parent.parent / "tools" / "fixtures" / "cards_raw.txt"
    cards, raw_count, dupe_count = parse_file(str(fixture))

    assert raw_count == 28, f"Expected 28 raw lines, got {raw_count}"
    assert len(cards) == 25, f"Expected 25 unique cards, got {len(cards)}"
    assert dupe_count == 3, f"Expected 3 dupes, got {dupe_count}"


def test_amex_15_digit() -> None:
    """Amex 15-digit parsed with CVV 7729."""
    line = "371759834256002|03|2029|7729|Emma Jacobs|6 Lake Kitchawan Dr|||10590|9146029051|egra11@icloud.com|"
    card = parse_line(line)

    assert card is not None
    assert card.pan == "371759834256002", f"Amex PAN wrong: {card.pan}"
    assert card.pan_len == 15, f"Amex length wrong: {card.pan_len}"
    assert card.cvv == "7729", f"Amex CVV wrong: {card.cvv}"


def test_leading_zero_cvv() -> None:
    """Leading-zero CVV preserved: '047' is valid 3."""
    line = "5181550173333950|03|2029|047|Tracy Stearns|1267 Normandy Rd|||62992|16186140144|stearnstracy@yahoo.com|"
    card = parse_line(line)

    assert card is not None
    assert card.cvv == "047", f"Leading zero CVV not preserved: {card.cvv}"


def test_ipv6_capture() -> None:
    """IPv6 addresses captured (both forward and dual)."""
    line = "371022263211018|04|2029|7992|Angelica Vargas|3311 S Westcrest Rd #256|West Valley City||||5628424052|angievargas7297@gmail.com|2601:681:5d01:5c40:e87:7a30:8d13:be5b"
    card = parse_line(line)

    assert card is not None
    assert card.ip == "2601:681:5d01:5c40:e87:7a30:8d13:be5b", f"IPv6 not captured: {card.ip}"


def test_hex_tokens_dropped() -> None:
    """Hex/garbage tokens like 6aca26e406625 dropped silently."""
    line = "5450460689249472|11|2028|996|6aca26e406625|73111|||"
    card = parse_line(line)

    assert card is not None
    # Hex token should not appear in any field
    assert "6aca26e406625" not in card.name
    assert "6aca26e406625" not in card.street


def test_zip4_preserved() -> None:
    """ZIP+4 parsed to zip+zip4 raw-preserved."""
    line = "371726515513001|09|2027|1906|Denise A McDonnell|7655 Williams Road|||71047-5513|8456119486|denise.mcdonnell@yahoo.com|"
    card = parse_line(line)

    assert card is not None
    assert card.zip == "71047", f"ZIP wrong: {card.zip}"
    assert card.zip4 == "5513", f"ZIP4 wrong: {card.zip4}"


def test_state_unknown_when_missing() -> None:
    """State UNKNOWN where missing (never guessed)."""
    line = "4737034075902651|12|2028|263|Patsy Duran|104 Star Pointe Drive|Mount Olive|NC||9565384370|aniueekarodrigueztudela@yahoo.es|"
    card = parse_line(line)

    assert card is not None
    assert card.state == "NC", f"State should be NC: {card.state}"


def test_co_co_duplicate() -> None:
    """'CO|CO' → city UNKNOWN."""
    line = "5194570017324226|05|2027|932|Marc Miller|15241 Telluride St,, Suite 100|CO|CO|80601|5821979633|lobello4406@gmail.com|"
    card = parse_line(line)

    assert card is not None
    assert card.state == "CO", f"State should be CO: {card.state}"
    # City becomes UNKNOWN when state duplicated
    assert card.city == "UNKNOWN", f"City should be UNKNOWN for CO|CO: {card.city}"


def test_comma_address_parsed() -> None:
    """Comma addresses parsed correctly."""
    line = "5194570017324226|05|2027|932|Marc Miller|15241 Telluride St, Suite 100|CO|80601|5821979633|lobello4406@gmail.com|"
    card = parse_line(line)

    assert card is not None
    assert "Telluride" in card.street, f"Street should contain Telluride: {card.street}"


def test_typo_email_kept_raw() -> None:
    """Typo email kept raw (yahohoo.como stays)."""
    line = "5156786297053667|05|2030|413|Stephan Dennis|2454 Lyons Rd|||13118|3156041289|vldennis73@yahohoo.como|"
    card = parse_line(line)

    assert card is not None
    assert card.email == "vldennis73@yahohoo.como", f"Typo email should be kept: {card.email}"


def test_junk_counters_dropped() -> None:
    """Leading junk counters (919, 877, 901) dropped."""
    line = "919 5178008141113983|08|2027|256|Kim Highstrom|519 S 4th Ave|Sioux Falls|SD|57104|||"
    card = parse_line(line)

    assert card is not None
    assert card.pan == "5178008141113983", f"PAN should not include counter: {card.pan}"
    assert "919" not in card.pan


def test_phone_formats() -> None:
    """Various phone formats normalized."""
    # Format: (063) 094-0738
    line1 = "4769708055487665|02|2030|527|Shawn Rivers|Flamingo Road||CA|93272|(063) 094-0738|larson.william@gmail.com|"
    card1 = parse_line(line1)
    assert card1 is not None
    assert card1.phone == "0630940738", f"Phone format 1 wrong: {card1.phone}"

    # Format: 476-271-8383
    line2 = "4466210604028112|08|2031|343|Louis Smith|5th Avenue||CA|10019|4762718383|heatherfranklin@icloud.com|"
    card2 = parse_line(line2)
    assert card2 is not None
    assert card2.phone == "4762718383", f"Phone format 2 wrong: {card2.phone}"


def test_city_only_state_unknown() -> None:
    """City-only lines → city set, state UNKNOWN."""
    line = "372983090371011|10|2029|8090|Anthony Serge|1233 N Bronough St Apt 1|Tallahassee|FL|US|9545888145|fouziashejarif@gmail.com|"
    card = parse_line(line)

    assert card is not None
    assert card.city == "Tallahassee", f"City should be Tallahassee: {card.city}"
    assert card.state == "FL", f"State should be FL: {card.state}"


def test_us_token_consumed() -> None:
    """Country token US after state/zip consumed, not stored as state."""
    line = "372983090371011|10|2029|8090|Anthony Serge|1233 N Bronough St Apt 1|Tallahassee|FL|US|9545888145|fouziashejarif@gmail.com|"
    card = parse_line(line)

    assert card is not None
    assert card.state == "FL", f"State should be FL, not US: {card.state}"


if __name__ == "__main__":
    import traceback

    tests = [
        test_raw_to_unique,
        test_amex_15_digit,
        test_leading_zero_cvv,
        test_ipv6_capture,
        test_hex_tokens_dropped,
        test_zip4_preserved,
        test_state_unknown_when_missing,
        test_co_co_duplicate,
        test_comma_address_parsed,
        test_typo_email_kept_raw,
        test_junk_counters_dropped,
        test_phone_formats,
        test_city_only_state_unknown,
        test_us_token_consumed,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed / {failed} failed")
    sys.exit(0 if failed == 0 else 1)
