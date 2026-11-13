from __future__ import annotations

from app.core.taxonomy import classify, derive_decline_code, extract_code, is_known_code
from app.core.verdicts import Verdict, charge_for, is_fault, is_terminal


def test_charge_matrix() -> None:
    assert charge_for(Verdict.APPROVED) == 1
    assert charge_for(Verdict.LIVE) == 1
    assert charge_for(Verdict.LIVE_3DS) == 0.5
    for verdict in (
        Verdict.DEAD,
        Verdict.RETRY,
        Verdict.KEY_DEAD,
        Verdict.GATE_FAULT,
        Verdict.INCONCLUSIVE,
    ):
        assert charge_for(verdict) == 0


def test_terminal_set() -> None:
    assert {v for v in Verdict if is_terminal(v)} == {
        Verdict.APPROVED,
        Verdict.LIVE,
        Verdict.LIVE_3DS,
        Verdict.DEAD,
    }


def test_fault_set() -> None:
    assert is_fault(Verdict.RETRY)
    assert is_fault(Verdict.INCONCLUSIVE)
    assert is_fault(Verdict.GATE_FAULT)
    assert not is_fault(Verdict.APPROVED)


def test_approve() -> None:
    assert classify(200, {}) == (Verdict.APPROVED, "ok")
    assert classify(201, {}) == (Verdict.APPROVED, "ok")


def test_insufficient_funds_is_live() -> None:
    body = {"error": {"code": "card_declined", "decline_code": "insufficient_funds"}}
    assert classify(402, body) == (Verdict.LIVE, "insufficient_funds")


def test_generic_decline_is_dead() -> None:
    body = {"error": {"code": "card_declined", "decline_code": "generic_decline"}}
    assert classify(402, body) == (Verdict.DEAD, "generic_decline")


def test_3ds() -> None:
    body = {"error": {"code": "authentication_required"}}
    assert classify(402, body) == (Verdict.LIVE_3DS, "authentication_required")


def test_key_dead_status() -> None:
    assert classify(401, {}) == (Verdict.KEY_DEAD, "unauthorized")
    assert classify(403, {"error": {"code": "invalid_api_key"}}) == (
        Verdict.KEY_DEAD,
        "invalid_api_key",
    )


def test_retry_statuses() -> None:
    for status in (429, 500, 502, 503, 504):
        assert classify(status, {}) == (Verdict.RETRY, f"http_{status}")


def test_unmapped_402_is_dead_with_prefix() -> None:
    verdict, code = classify(402, {"error": {"code": "weird_new_code"}})
    assert verdict is Verdict.DEAD
    assert code.startswith("unmapped:")


def test_bad_request_is_retry() -> None:
    verdict, code = classify(400, {"error": {"code": "parameter_invalid"}})
    assert verdict is Verdict.RETRY
    assert code.startswith("bad_request:")


def test_decline_code_preferred_over_code() -> None:
    body = {"error": {"code": "card_declined", "decline_code": "do_not_honor"}}
    assert extract_code(body) == "do_not_honor"
    assert classify(402, body)[0] is Verdict.DEAD


def test_message_fallback() -> None:
    assert derive_decline_code("Insufficient funds on this card") == "insufficient_funds"
    assert derive_decline_code("Do Not Honor") == "do_not_honor"
    assert derive_decline_code("Issuer requires 3D Secure") == "authentication_required"
    assert derive_decline_code("something nobody mapped") == ""


def test_known_code() -> None:
    assert is_known_code("insufficient_funds")
    assert not is_known_code("weird_new_code")


def test_message_only_error_envelope_derives_code() -> None:
    body = {"error": {"message": "Your card has insufficient funds."}}
    assert extract_code(body) == "insufficient_funds"
    assert classify(402, body)[0] is Verdict.LIVE


def test_errors_string_envelope() -> None:
    assert extract_code({"errors": "Pickup Card"}) == "pickup card"


def test_errors_list_envelope() -> None:
    body = {"errors": [{"code": "Do_Not_Honor"}]}
    assert extract_code(body) == "do_not_honor"
    assert classify(402, body) == (Verdict.DEAD, "do_not_honor")


def test_402_without_code_is_unmapped_dead() -> None:
    assert classify(402, {}) == (Verdict.DEAD, "unmapped:no_code")


def test_400_without_code_is_bad_request_retry() -> None:
    assert classify(400, {}) == (Verdict.RETRY, "bad_request:")


def test_known_key_dead_code_on_402() -> None:
    body = {"error": {"code": "invalid_api_key"}}
    assert classify(402, body) == (Verdict.KEY_DEAD, "invalid_api_key")


def test_known_retry_code_on_402() -> None:
    body = {"error": {"code": "processing_error"}}
    assert classify(402, body) == (Verdict.RETRY, "processing_error")


def test_unexpected_status_is_retry() -> None:
    assert classify(418, {}) == (Verdict.RETRY, "http_418")
