"""Provider-code taxonomy (section 8) — the heart of the build.

``classify`` is deliberately ordered: transport status first, then the provider
code. Unknown codes become RETRY and must trigger an admin alert after five
occurrences, because an unknown code is a bug in this map, not a user problem.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from .verdicts import Verdict

DEAD: Final[frozenset[str]] = frozenset(
    {
        "generic_decline",
        "do_not_honor",
        "pickup_card",
        "lost_card",
        "stolen_card",
        "card_not_supported",
        "restricted_card",
        "security_violation",
        "service_not_allowed",
        "transaction_not_allowed",
        "fraudulent",
        "incorrect_number",
        "incorrect_cvc",
        "invalid_cvc",
        "invalid_expiry_month",
        "invalid_expiry_year",
        "expired_card",
        "currency_not_supported",
        "card_velocity_exceeded",
        "incorrect_zip",
        "new_account_information_available",
        "duplicate_transaction",
        "invalid_account",
        "no_account",
        "account_closed",
        "rejected",
        "not_permitted",
        "declined",
    }
)

LIVE: Final[frozenset[str]] = frozenset(
    {
        "insufficient_funds",
        "withdrawal_count_limit_exceeded",
        "call_issuer",
        "approve_with_id",
        "reenter_transaction",
        "not_sufficient_funds",
    }
)

RETRY: Final[frozenset[str]] = frozenset(
    {
        "processing_error",
        "try_again_later",
        "issuer_unavailable",
        "rate_limit",
        "timeout",
        "acquirer_error",
        "system_error",
    }
)

THREE_DS: Final[frozenset[str]] = frozenset(
    {"authentication_required", "authentication_not_handled", "challenge_required"}
)

KEY_DEAD: Final[frozenset[str]] = frozenset(
    {
        "invalid_api_key",
        "api_key_expired",
        "account_invalid",
        "unauthorized",
        "merchant_not_active",
        "more_permissions_required",
        "invalid_merchant",
    }
)

# Free-text fallback for processors that return no machine code. Ordered:
# first match wins, so put the specific patterns before the generic ones.
_MESSAGE_MAP: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"insufficient|not sufficient|no funds", re.I), "insufficient_funds"),
    (
        re.compile(r"3d secure|3ds|authentication required|challenge", re.I),
        "authentication_required",
    ),
    (re.compile(r"do not honor|do_not_honor", re.I), "do_not_honor"),
    (re.compile(r"pick ?up", re.I), "pickup_card"),
    (re.compile(r"lost card", re.I), "lost_card"),
    (re.compile(r"stolen card", re.I), "stolen_card"),
    (re.compile(r"expired", re.I), "expired_card"),
    (re.compile(r"incorrect (cvv|cvc|security code)", re.I), "incorrect_cvc"),
    (re.compile(r"incorrect (zip|postal)", re.I), "incorrect_zip"),
    (re.compile(r"invalid (card )?number|invalid account", re.I), "incorrect_number"),
    (re.compile(r"velocity|too many", re.I), "card_velocity_exceeded"),
    (re.compile(r"fraud", re.I), "fraudulent"),
    (re.compile(r"restricted|not permitted|not allowed", re.I), "restricted_card"),
    (re.compile(r"unsupported|not supported", re.I), "card_not_supported"),
    (re.compile(r"try again|later", re.I), "try_again_later"),
    (re.compile(r"timeout|timed out", re.I), "timeout"),
    (re.compile(r"processing error|system error", re.I), "processing_error"),
    (re.compile(r"declin", re.I), "declined"),
)

_GENERIC_CODES: Final[frozenset[str]] = DEAD | LIVE | RETRY | THREE_DS | KEY_DEAD


def extract_decline_code(body: Mapping[str, object]) -> str:
    """The issuer's decline reason, when the processor supplies one."""
    raw_error = body.get("error")
    if isinstance(raw_error, Mapping):
        value = raw_error.get("decline_code")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def extract_code(body: Mapping[str, object]) -> str:
    """Pull the most specific provider code out of an error envelope."""
    raw_error = body.get("error")
    if isinstance(raw_error, Mapping):
        for field in ("decline_code", "code", "type"):
            value = raw_error.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        message = raw_error.get("message")
        if isinstance(message, str) and message.strip():
            return derive_decline_code(message)
    errors = body.get("errors")
    if isinstance(errors, str) and errors.strip():
        return errors.strip().lower()
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, Mapping):
            for field in ("code", "decline_code", "message"):
                value = first.get(field)
                if isinstance(value, str) and value.strip():
                    return value.strip().lower()
    return ""


def derive_decline_code(message: str) -> str:
    for pattern, code in _MESSAGE_MAP:
        if pattern.search(message):
            return code
    return ""


def classify(status: int, body: Mapping[str, object]) -> tuple[Verdict, str]:
    """Map an HTTP status plus error envelope to a terminal-or-not verdict."""
    code = extract_code(body)

    if status in (200, 201):
        return Verdict.APPROVED, "ok"
    if status in (429, 500, 502, 503, 504):
        return Verdict.RETRY, f"http_{status}"
    if status in (401, 403):
        return Verdict.KEY_DEAD, code or "unauthorized"
    if code in KEY_DEAD:
        return Verdict.KEY_DEAD, code
    if code in THREE_DS:
        return Verdict.LIVE_3DS, code
    if code in DEAD:
        return Verdict.DEAD, code
    if code in LIVE:
        return Verdict.LIVE, code
    if code in RETRY:
        return Verdict.RETRY, code
    if status == 402:
        return Verdict.DEAD, f"unmapped:{code or 'no_code'}"
    if status == 400:
        return Verdict.RETRY, f"bad_request:{code}"
    return Verdict.RETRY, f"http_{status}"


def is_known_code(code: str) -> bool:
    return code in _GENERIC_CODES
