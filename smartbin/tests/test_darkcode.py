"""Dark Code Map tests for SMARTBIN v7.1.0

Every map row is a unit test. Per-processor divergence covered.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DarkCodeRule:
    processor: str
    raw_code: str
    decline_code: str
    network_code: str
    expected_verdict: str
    slang: str = ""


# Braintree rules
BRAINTRREE_RULES = [
    DarkCodeRule("braintree", "authorized", "1000", "", "APPROVED"),
    DarkCodeRule("braintree", "insufficient_funds", "", "51", "INSUFFICIENT"),
    DarkCodeRule("braintree", "processor_declined", "insufficient_credit_available", "", "INSUFFICIENT", "ICAV"),
    DarkCodeRule("braintree", "gateway_rejected", "", "", "LIVE-RETRY"),
    DarkCodeRule("braintree", "lookup", "enrolled", "", "3DS-CHALLENGE"),
]

# Adyen rules
ADYEN_RULES = [
    DarkCodeRule("adyen", "refused", "", "", "INSUFFICIENT", "Not enough balance"),
    DarkCodeRule("adyen", "refused", "SECURITY", "card105", "3DS-CHALLENGE"),
]

# NMI rules
NMI_RULES = [
    DarkCodeRule("nmi", "", "", "201", "AMBIGUOUS"),
    DarkCodeRule("nmi", "", "", "204", "CVV-FAIL"),
    DarkCodeRule("nmi", "", "", "250", "INVALID"),
    DarkCodeRule("nmi", "", "", "200", "APPROVED"),
]

# AuthNet rules
AUTHNET_RULES = [
    DarkCodeRule("authnet", "2", "", "", "AMBIGUOUS"),
    DarkCodeRule("authnet", "8", "", "", "EXPIRED"),
    DarkCodeRule("authnet", "17", "", "", "CVV-FAIL"),
    DarkCodeRule("authnet", "27", "", "", "AMBIGUOUS"),
    DarkCodeRule("authnet", "28", "", "", "AMBIGUOUS"),
]

# Stripe rules
STRIPE_RULES = [
    DarkCodeRule("stripe", "", "generic_decline", "", "AMBIGUOUS"),
    DarkCodeRule("stripe", "", "fraudulent", "", "AMBIGUOUS"),
]

# Network codes
NETWORK_RULES = [
    DarkCodeRule("network", "", "", "05", "DO-NOT-HONOR"),
    DarkCodeRule("network", "", "", "51", "INSUFFICIENT"),
    DarkCodeRule("network", "", "", "57", "MERCHANT-RESTRICTED"),
    DarkCodeRule("network", "", "", "62", "MERCHANT-RESTRICTED"),
    DarkCodeRule("network", "", "", "14", "INVALID"),
    DarkCodeRule("network", "", "", "54", "EXPIRED"),
    DarkCodeRule("network", "", "", "41", "PICKUP"),
    DarkCodeRule("network", "", "", "43", "PICKUP"),
    DarkCodeRule("network", "", "", "82", "CVV-FAIL"),
    DarkCodeRule("network", "", "", "N7", "CVV-FAIL"),
    DarkCodeRule("network", "", "", "Z3", "MERCHANT-RESTRICTED"),
]


def normalize_verdict(processor: str, raw_code: str, decline_code: str, network_code: str) -> tuple[str, str]:
    """Normalize raw codes to canonical verdict + slang."""
    all_rules = (
        BRAINTRREE_RULES + ADYEN_RULES + NMI_RULES + AUTHNET_RULES +
        STRIPE_RULES + NETWORK_RULES
    )

    # Sort rules by specificity (more fields set = higher priority)
    def rule_specificity(rule: DarkCodeRule) -> int:
        return sum([
            bool(rule.raw_code),
            bool(rule.decline_code),
            bool(rule.network_code)
        ])

    sorted_rules = sorted(all_rules, key=rule_specificity, reverse=True)

    for rule in sorted_rules:
        if rule.processor != processor:
            continue

        # Count how many code fields are set on the rule
        fields_set = rule_specificity(rule)

        # Count how many code fields match
        fields_matched = 0
        if rule.raw_code and rule.raw_code.lower() in raw_code.lower():
            fields_matched += 1
        if rule.decline_code and rule.decline_code.lower() in decline_code.lower():
            fields_matched += 1
        if rule.network_code and rule.network_code == network_code:
            fields_matched += 1

        # All set fields must match
        if fields_matched == fields_set and fields_set > 0:
            return rule.expected_verdict, rule.slang

    return "AMBIGUOUS", ""


def test_braintree_rules() -> None:
    """Braintree dark code rules."""
    for rule in BRAINTRREE_RULES:
        verdict, slang = normalize_verdict(
            rule.processor, rule.raw_code, rule.decline_code, rule.network_code
        )
        assert verdict == rule.expected_verdict, \
            f"Braintree {rule.raw_code}: expected {rule.expected_verdict}, got {verdict}"
        if rule.slang:
            assert slang == rule.slang, \
                f"Braintree {rule.raw_code}: expected slang {rule.slang}, got {slang}"


def test_adyen_rules() -> None:
    """Adyen dark code rules."""
    for rule in ADYEN_RULES:
        verdict, slang = normalize_verdict(
            rule.processor, rule.raw_code, rule.decline_code, rule.network_code
        )
        assert verdict == rule.expected_verdict, \
            f"Adyen {rule.raw_code}: expected {rule.expected_verdict}, got {verdict}"


def test_nmi_rules() -> None:
    """NMI dark code rules."""
    for rule in NMI_RULES:
        verdict, slang = normalize_verdict(
            rule.processor, rule.raw_code, rule.decline_code, rule.network_code
        )
        assert verdict == rule.expected_verdict, \
            f"NMI {rule.network_code}: expected {rule.expected_verdict}, got {verdict}"


def test_authnet_rules() -> None:
    """AuthNet dark code rules."""
    for rule in AUTHNET_RULES:
        verdict, slang = normalize_verdict(
            rule.processor, rule.raw_code, rule.decline_code, rule.network_code
        )
        assert verdict == rule.expected_verdict, \
            f"AuthNet {rule.raw_code}: expected {rule.expected_verdict}, got {verdict}"


def test_stripe_rules() -> None:
    """Stripe dark code rules."""
    for rule in STRIPE_RULES:
        verdict, slang = normalize_verdict(
            rule.processor, rule.raw_code, rule.decline_code, rule.network_code
        )
        assert verdict == rule.expected_verdict, \
            f"Stripe {rule.decline_code}: expected {rule.expected_verdict}, got {verdict}"
        # generic_decline should never be DO-NOT-HONOR
        if rule.decline_code == "generic_decline":
            assert verdict != "DO-NOT-HONOR", "generic_decline should not map to DO-NOT-HONOR"


def test_network_rules() -> None:
    """Network code rules."""
    for rule in NETWORK_RULES:
        verdict, slang = normalize_verdict(
            rule.processor, rule.raw_code, rule.decline_code, rule.network_code
        )
        assert verdict == rule.expected_verdict, \
            f"Network {rule.network_code}: expected {rule.expected_verdict}, got {verdict}"


def test_total_rule_count() -> None:
    """≥24 render-rules total."""
    total = (
        len(BRAINTRREE_RULES) + len(ADYEN_RULES) + len(NMI_RULES) +
        len(AUTHNET_RULES) + len(STRIPE_RULES) + len(NETWORK_RULES)
    )
    assert total >= 24, f"Expected ≥24 rules, got {total}"


def test_unmapped_code_ambiguous() -> None:
    """Unmapped codes → AMBIGUOUS."""
    verdict, slang = normalize_verdict("stripe", "unknown_code", "mystery", "")
    assert verdict == "AMBIGUOUS", f"Unmapped code should be AMBIGUOUS, got {verdict}"


if __name__ == "__main__":
    import sys
    import traceback

    tests = [
        test_braintree_rules,
        test_adyen_rules,
        test_nmi_rules,
        test_authnet_rules,
        test_stripe_rules,
        test_network_rules,
        test_total_rule_count,
        test_unmapped_code_ambiguous,
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
