"""Stripe adapter contract (section 24).

Every case from the test matrix is represented here: approve,
insufficient_funds, generic_decline, authentication_required, 401, 429, 500,
timeout and a malformed body. The adapter only reshapes; the taxonomy decides.
"""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
import respx
from app.config import Settings
from app.core.parse import Card
from app.core.taxonomy import classify
from app.core.verdicts import Verdict
from app.providers.base import Key
from app.providers.stripe import StripeAdapter

BASE = "https://api.stripe.com"
SETUP = f"{BASE}/v1/setup_intents"
INTENT = f"{BASE}/v1/payment_intents"
BALANCE = f"{BASE}/v1/balance"
METHODS = f"{BASE}/v1/payment_methods"

CARD = Card.parse("4242424242424242|11|29|716")


def settings() -> Settings:
    return Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        stripe_secret_key="sk_live_test",
    )


def adapter() -> StripeAdapter:
    return StripeAdapter(settings())


def key() -> Key:
    return Key(id=1, gate_id="auth_stripe", secret="sk_live_test", country="US")


def method_ok() -> respx.Route:
    """The card PaymentMethod that every auth creates before confirming."""
    return respx.post(METHODS).mock(
        return_value=httpx.Response(200, json={"id": "pm_1", "object": "payment_method"})
    )


@respx.mock
async def test_approve_setup_intent() -> None:
    method_ok()
    respx.post(SETUP).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "seti_1",
                "object": "setup_intent",
                "status": "succeeded",
                "livemode": True,
                "payment_method": {
                    "checks": {"cvc_check": "pass", "address_postal_code_check": "pass"}
                },
            },
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 200
    assert raw.provider_ref == "seti_1"
    assert raw.avs == "pass"
    assert raw.cvc == "pass"
    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_insufficient_funds_is_live() -> None:
    method_ok()
    respx.post(SETUP).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "seti_2",
                "status": "requires_payment_method",
                "livemode": True,
                "last_setup_error": {
                    "code": "card_declined",
                    "decline_code": "insufficient_funds",
                    "message": "Your card has insufficient funds.",
                },
            },
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 402
    assert classify(raw.status, raw.body) == (Verdict.LIVE, "insufficient_funds")


@respx.mock
async def test_generic_decline_is_dead() -> None:
    method_ok()
    respx.post(SETUP).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "seti_3",
                "status": "requires_payment_method",
                "last_setup_error": {
                    "code": "card_declined",
                    "decline_code": "generic_decline",
                    "message": "Your card was declined.",
                },
            },
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "generic_decline")


@respx.mock
async def test_authentication_required_carries_acs_url() -> None:
    method_ok()
    respx.post(SETUP).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "seti_4",
                "status": "requires_action",
                "livemode": True,
                "next_action": {
                    "type": "redirect_to_url",
                    "redirect_to_url": {"url": "https://acs.example/challenge"},
                },
            },
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 402
    assert raw.acs_url == "https://acs.example/challenge"
    assert raw.three_ds == "C"
    assert classify(raw.status, raw.body) == (Verdict.LIVE_3DS, "authentication_required")


@respx.mock
async def test_401_is_key_dead() -> None:
    method_ok()
    respx.post(SETUP).mock(
        return_value=httpx.Response(
            401,
            json={"error": {"code": "invalid_api_key", "message": "Invalid API Key"}},
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body) == (Verdict.KEY_DEAD, "invalid_api_key")


@respx.mock
async def test_429_is_retry() -> None:
    method_ok()
    respx.post(SETUP).mock(return_value=httpx.Response(429, json={"error": {"code": "rate_limit"}}))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_429")


@respx.mock
async def test_500_is_retry() -> None:
    method_ok()
    respx.post(SETUP).mock(
        return_value=httpx.Response(500, json={"error": {"code": "system_error"}})
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_500")


@respx.mock
async def test_timeout_raises_so_executor_can_mark_inconclusive() -> None:
    method_ok()
    respx.post(SETUP).mock(side_effect=httpx.ConnectTimeout("no route"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPError):
            await adapter().auth(client, key(), CARD, 0, False)


@respx.mock
async def test_malformed_200_is_retry_not_dead() -> None:
    method_ok()
    respx.post(SETUP).mock(return_value=httpx.Response(200, text="<html>proxy</html>"))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 502
    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_502")


@respx.mock
async def test_capture_path_uses_manual_capture() -> None:
    method_ok()
    route = respx.post(INTENT).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "pi_1",
                "status": "requires_capture",
                "livemode": True,
                "payment_method": {"checks": {"cvc_check": "pass"}},
            },
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 500, True)

    assert route.called
    request = route.calls[0].request
    assert request.headers["Stripe-Version"] == "2024-06-20"
    body = request.content.decode()
    assert "capture_method=manual" in body
    assert "amount=500" in body
    assert raw.status == 200
    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_validate_key_requires_livemode() -> None:
    respx.get(BALANCE).mock(
        return_value=httpx.Response(200, json={"object": "balance", "livemode": True})
    )
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is True


@respx.mock
async def test_validate_key_rejects_test_mode() -> None:
    respx.get(BALANCE).mock(
        return_value=httpx.Response(200, json={"object": "balance", "livemode": False})
    )
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False


@respx.mock
async def test_validate_key_rejects_401() -> None:
    respx.get(BALANCE).mock(
        return_value=httpx.Response(401, json={"error": {"code": "invalid_api_key"}})
    )
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False


@respx.mock
async def test_cancel_setup_intent() -> None:
    route = respx.post(f"{SETUP}/seti_9/cancel").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as client:
        await adapter().cancel(client, key(), "seti_9")
    assert route.called


@respx.mock
async def test_cancel_payment_intent_and_empty_ref() -> None:
    route = respx.post(f"{INTENT}/pi_9/cancel").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as client:
        await adapter().cancel(client, key(), "pi_9")
        await adapter().cancel(client, key(), "")
    assert route.call_count == 1


@respx.mock
async def test_capture_returns_mapped_intent() -> None:
    respx.post(f"{INTENT}/pi_7/capture").mock(
        return_value=httpx.Response(200, json={"id": "pi_7", "status": "succeeded"})
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().capture(client, key(), "pi_7", 500)
    assert raw.status == 200
    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_unknown_intent_status_is_generic_decline() -> None:
    method_ok()
    respx.post(SETUP).mock(
        return_value=httpx.Response(
            200, json={"id": "seti_5", "status": "canceled", "livemode": True}
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)
    assert classify(raw.status, raw.body) == (Verdict.DEAD, "generic_decline")


@respx.mock
async def test_validate_key_network_error_is_false() -> None:
    respx.get(BALANCE).mock(side_effect=httpx.ConnectTimeout("no route"))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False


@respx.mock
async def test_setup_intent_confirms_with_return_url_and_card_type() -> None:
    method_ok()
    # A confirmed card that the issuer challenges needs a return_url, and the
    # type is pinned so account-level automatic methods cannot change the call.
    route = respx.post(SETUP).mock(
        return_value=httpx.Response(
            200, json={"id": "seti_6", "status": "succeeded", "livemode": True}
        )
    )
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key(), CARD, 0, False)

    form = parse_qs(route.calls[0].request.content.decode())
    assert form["confirm"] == ["true"]
    assert form["return_url"] == ["https://smartbin-eurr.onrender.com/"]
    assert form["payment_method_types[]"] == ["card"]
    assert form["payment_method"] == ["pm_1"]
    # SetupIntent usage defaults to off_session; sending nothing is safer.
    assert "usage" not in form
    # Card data must not ride along on the intent: it belongs to the PaymentMethod.
    assert not any(key.startswith("payment_method_data") for key in form)


@respx.mock
async def test_capture_intent_uses_return_url_and_no_setup_only_params() -> None:
    method_ok()
    # ``usage`` is a SetupIntent parameter. Sending it to /v1/payment_intents
    # would be rejected as an unknown parameter on every kill/charge.
    route = respx.post(INTENT).mock(
        return_value=httpx.Response(
            200, json={"id": "pi_2", "status": "requires_capture", "livemode": True}
        )
    )
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key(), CARD, 500, True)

    form = parse_qs(route.calls[0].request.content.decode())
    assert form["confirm"] == ["true"]
    assert form["capture_method"] == ["manual"]
    assert form["amount"] == ["500"]
    assert form["return_url"] == ["https://smartbin-eurr.onrender.com/"]
    assert form["payment_method_types[]"] == ["card"]
    assert form["payment_method"] == ["pm_1"]
    assert "usage" not in form


@respx.mock
async def test_return_url_is_configurable() -> None:
    method_ok()
    route = respx.post(SETUP).mock(
        return_value=httpx.Response(
            200, json={"id": "seti_7", "status": "succeeded", "livemode": True}
        )
    )
    custom = StripeAdapter(
        Settings(
            _env_file=None,
            live_only=False,
            tg_token="",
            stripe_secret_key="sk_live_test",
            stripe_return_url="https://example.test/back",
        )
    )
    async with httpx.AsyncClient() as client:
        await custom.auth(client, key(), CARD, 0, False)

    form = parse_qs(route.calls[0].request.content.decode())
    assert form["return_url"] == ["https://example.test/back"]


@respx.mock
async def test_card_is_validated_by_the_payment_method_call() -> None:
    # An invalid card is answered on the PaymentMethod step; confirming the
    # intent never happens and the user sees a normal decline envelope.
    respx.post(METHODS).mock(
        return_value=httpx.Response(
            402,
            json={
                "error": {
                    "type": "card_error",
                    "code": "card_declined",
                    "decline_code": "incorrect_number",
                    "param": "number",
                    "message": "Your card number is incorrect.",
                }
            },
        )
    )
    setup = respx.post(SETUP).mock(return_value=httpx.Response(200, json={}))

    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert setup.call_count == 0
    assert raw.status == 402
    assert classify(raw.status, raw.body) == (Verdict.DEAD, "incorrect_number")


@respx.mock
async def test_payment_method_without_an_id_is_a_retry() -> None:
    # A 200 with no id means the body was not a PaymentMethod (proxy page),
    # which is a transport fault and must never be read as a declined card.
    respx.post(METHODS).mock(return_value=httpx.Response(200, json={"object": "payment_method"}))

    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 502
    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_502")


@respx.mock
async def test_payment_method_carries_the_postal_code() -> None:
    route = method_ok()
    respx.post(SETUP).mock(
        return_value=httpx.Response(
            200, json={"id": "seti_8", "status": "succeeded", "livemode": True}
        )
    )
    card = Card.parse("4242424242424242|11|29|716|90210")
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key(), card, 0, False)

    form = parse_qs(route.calls[0].request.content.decode())
    assert form["billing_details[address][postal_code]"] == ["90210"]
    assert form["card[number]"] == ["4242424242424242"]
