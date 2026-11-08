from __future__ import annotations

from typing import Any

from models import CheckOutcome

NETWORK_CODES: dict[str, tuple[str, int, str]] = {
    "00": ("APPROVED", 1, "approved"),
    "05": ("DO-NOT-HONOR", 0, "retry-once-clean-gate"),
    "51": ("INSUFFICIENT", 1, "no-funds"),
    "14": ("INVALID", 0, "dead"),
    "56": ("INVALID", 0, "dead"),
    "54": ("EXPIRED", 0, "dead"),
    "41": ("PICKUP", 0, "dead-or-reported"),
    "43": ("PICKUP", 0, "dead-or-reported"),
    "82": ("CVV-FAIL", 1, "live-cvv-wrong"),
    "N7": ("CVV-FAIL", 1, "live-cvv-wrong"),
    "57": ("MERCHANT-RESTRICTED", 1, "not-card-health-signal"),
    "62": ("MERCHANT-RESTRICTED", 1, "not-card-health-signal"),
    "Z3": ("MERCHANT-RESTRICTED", 1, "not-card-health-signal"),
}

STRIPE_CODES: dict[str, tuple[str, int, str]] = {
    "generic_decline": ("AMBIGUOUS", 0, "second-gate-required"),
    "fraudulent": ("AMBIGUOUS", 0, "second-gate-required"),
    "do_not_honor": ("AMBIGUOUS", 0, "second-gate-required"),
    "insufficient_funds": ("INSUFFICIENT", 1, "no-funds"),
    "expired_card": ("EXPIRED", 0, "dead-by-date"),
    "incorrect_cvc": ("CVV-FAIL", 1, "live-cvv-wrong"),
    "incorrect_number": ("INVALID", 0, "dead"),
    "invalid_number": ("INVALID", 0, "dead"),
    "pickup_card": ("PICKUP", 0, "dead-or-reported"),
    "lost_card": ("PICKUP", 0, "dead-or-reported"),
    "stolen_card": ("PICKUP", 0, "dead-or-reported"),
    "card_not_supported": ("MERCHANT-RESTRICTED", 1, "not-card-health-signal"),
    "restricted_card": ("MERCHANT-RESTRICTED", 1, "not-card-health-signal"),
    "security_violation": ("MERCHANT-RESTRICTED", 1, "not-card-health-signal"),
    "processing_error": ("INCONCLUSIVE", 0, "retry-or-degrade"),
    "authentication_required": ("3DS-CHALLENGE", 1, "capture-acs"),
}


def _outcome(
    verdict: str,
    live: int,
    action: str,
    result: dict[str, Any],
    canary_ok: int,
    note: str = "",
) -> CheckOutcome:
    return CheckOutcome(
        verdict=verdict,
        live=live,
        action=action,
        raw_code=str(result.get("raw_code") or ""),
        decline_code=str(result.get("decline_code") or ""),
        twofa_read=str(result.get("twofa_read") or ""),
        amount_band=int(result.get("amount_band") or 0),
        canary_ok=canary_ok,
        acs_host=str(result.get("acs_host") or ""),
        latency_ms=int(result.get("latency_ms") or 0),
        proxy_id=int(result.get("proxy_id") or 0),
        note=note,
        retry=bool(result.get("retry") or False),
        gate=str(result.get("gate") or ""),
    )


def map_result(result: dict[str, Any], canary_ok: int = 1) -> CheckOutcome:
    """The only place a raw code becomes a verdict. Stripe scope never fakes a network code."""
    raw_code = str(result.get("raw_code") or "").upper()
    decline_code = str(result.get("decline_code") or "").lower()
    acs_host = str(result.get("acs_host") or "")
    timeout = bool(result.get("timeout"))
    approved = bool(result.get("approved"))

    if timeout:
        out = _outcome("INCONCLUSIVE", 0, "retry-once-different-gate", result, canary_ok, "timeout")
        out.retry = True
        return out

    # A 05 in a failed canary window is ambiguous, not a card verdict.
    if raw_code == "05" and canary_ok == 0:
        return _outcome(
            "AMBIGUOUS", 0, "canary-window-failed", result, canary_ok,
            "network 05 in failed canary window",
        )

    if canary_ok == 0:
        return _outcome(
            "INCONCLUSIVE", 0, "gate-window-failed", result, canary_ok,
            "dispatched inside failed canary window",
        )

    if acs_host:
        result = dict(result)
        result["twofa_read"] = "VBV"
        return _outcome(
            "3DS-CHALLENGE", 1, "capture-acs", result, canary_ok, "real ACS host captured",
        )

    if approved:
        result = dict(result)
        result["twofa_read"] = "NON-VBV"
        return _outcome("APPROVED", 1, "approved", result, canary_ok, "auth-and-void clean")

    if decline_code:
        verdict, live, action = STRIPE_CODES.get(
            decline_code, ("AMBIGUOUS", 0, "second-gate-required")
        )
        return _outcome(
            verdict, live, action, result, canary_ok,
            f"stripe scope decline={decline_code}",
        )

    if raw_code:
        verdict, live, action = NETWORK_CODES.get(
            raw_code, ("AMBIGUOUS", 0, "second-gate-required")
        )
        note = f"network code={raw_code}"
        out = _outcome(verdict, live, action, result, canary_ok, note)
        if verdict == "DO-NOT-HONOR":
            out.retry = True
        return out

    return _outcome("INCONCLUSIVE", 0, "retry-or-degrade", result, canary_ok, "empty gate response")
