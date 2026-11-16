"""Checkout.com adapter contract (section 24)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from app.config import Settings
from app.core.parse import Card
from app.core.taxonomy import classify
from app.core.verdicts import Verdict
from app.providers.base import Key
from app.providers.checkout import CheckoutAdapter, refusal_code

BASE = "https://api.checkout.com"
PAYMENTS = f"{BASE}/payments"
CARD = Card.parse("4242424242424242|11|29|716")


def settings() -> Settings:
    return Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        checkout_secret_key="sk_sbox_test",
        checkout_currency="USD",
    )


def adapter() -> CheckoutAdapter:
    return CheckoutAdapter(settings())


def key() -> Key:
    return Key(id=1, gate_id="charge_checkout", secret="sk_sbox_test", country="US")


def payment(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "pay_1",
        "status": "Authorized",
        "source": {"avs_check": "Y", "cvv_check": "M"},
    }
    base.update(overrides)
    return base


@respx.mock
async def test_authorized_is_approved() -> None:
    respx.post(PAYMENTS).mock(return_value=httpx.Response(201, json=payment()))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert raw.status == 200
    assert raw.provider_ref == "pay_1"
    assert raw.avs == "Y"
    assert raw.cvc == "M"
    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_captured_is_approved() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(201, json=payment(status="Captured"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, True)

    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_insufficient_funds_is_live() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            201,
            json=payment(status="Declined", response_summary="Insufficient funds"),
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.LIVE, "insufficient_funds")


@respx.mock
async def test_numeric_code_maps_without_summary() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            201,
            json=payment(status="Declined", response_code="20051", response_summary=""),
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.LIVE, "insufficient_funds")


@respx.mock
async def test_expired_card_is_dead() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            201, json=payment(status="Declined", response_summary="Card expired")
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "expired_card")


@respx.mock
async def test_generic_decline_is_dead() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            201, json=payment(status="Declined", response_summary="Declined")
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "generic_decline")


@respx.mock
async def test_3ds_pending_carries_redirect() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            202,
            json=payment(
                status="Pending",
                **{"3ds": {"redirect_url": "https://acs.example/challenge"}},
            ),
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert raw.status == 402
    assert raw.acs_url == "https://acs.example/challenge"
    assert classify(raw.status, raw.body) == (Verdict.LIVE_3DS, "authentication_required")


@respx.mock
async def test_pending_without_challenge_is_retry() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(202, json=payment(status="Pending"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert raw.status == 202
    assert classify(raw.status, raw.body)[0] is Verdict.RETRY


@respx.mock
async def test_401_is_key_dead() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(401, json={"error_type": "unauthorized"})
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body)[0] is Verdict.KEY_DEAD


@respx.mock
async def test_422_card_validation_is_dead() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            422, json={"error_type": "card_number_invalid"}
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "incorrect_number")


@respx.mock
async def test_429_is_retry() -> None:
    respx.post(PAYMENTS).mock(return_value=httpx.Response(429, json={}))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_429")


@respx.mock
async def test_500_is_retry() -> None:
    respx.post(PAYMENTS).mock(return_value=httpx.Response(500, json={}))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_500")


@respx.mock
async def test_timeout_raises_so_executor_marks_inconclusive() -> None:
    respx.post(PAYMENTS).mock(side_effect=httpx.ConnectTimeout("no route"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPError):
            await adapter().auth(client, key(), CARD, 100, False)


@respx.mock
async def test_malformed_200_is_retry_not_dead() -> None:
    respx.post(PAYMENTS).mock(return_value=httpx.Response(200, text="<html>proxy</html>"))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert raw.status == 502
    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_502")


@respx.mock
async def test_auth_sends_minor_units_and_capture_flag() -> None:
    route = respx.post(PAYMENTS).mock(return_value=httpx.Response(201, json=payment()))
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key(), CARD, 250, False)

    payload = json.loads(route.calls[0].request.content)
    assert payload["amount"] == 250
    assert payload["currency"] == "USD"
    assert payload["capture"] is False
    assert payload["source"]["number"] == "4242424242424242"
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk_sbox_test"


@respx.mock
async def test_capture_and_void_paths() -> None:
    capture = respx.post(f"{PAYMENTS}/pay_1/captures").mock(
        return_value=httpx.Response(202, json=payment(status="Captured"))
    )
    void = respx.post(f"{PAYMENTS}/pay_1/voids").mock(return_value=httpx.Response(202, json={}))
    async with httpx.AsyncClient() as client:
        await adapter().capture(client, key(), "pay_1", 250)
        await adapter().cancel(client, key(), "pay_1")
        await adapter().cancel(client, key(), "")

    assert capture.called and void.call_count == 1


@respx.mock
async def test_validate_key_uses_free_payments_read() -> None:
    route = respx.get(PAYMENTS).mock(return_value=httpx.Response(200, json={"data": []}))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is True
    assert route.called


@respx.mock
async def test_validate_key_rejects_401_and_network() -> None:
    respx.get(PAYMENTS).mock(return_value=httpx.Response(401, json={}))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False
    respx.get(PAYMENTS).mock(side_effect=httpx.ConnectTimeout("no route"))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False


@pytest.mark.parametrize(
    ("summary", "code"),
    [
        ("Insufficient funds", "insufficient_funds"),
        ("Card expired", "expired_card"),
        ("card_number_invalid", "incorrect_number"),
        ("Invalid CVC", "incorrect_cvc"),
        ("Transaction not allowed", "transaction_not_allowed"),
        ("Restricted card", "restricted_card"),
        ("Lost card", "lost_card"),
        ("Stolen card", "stolen_card"),
        ("Fraud suspected", "fraudulent"),
        ("Acquirer error", "acquirer_error"),
        ("Issuer unavailable", "issuer_unavailable"),
        ("Declined", "generic_decline"),
        ("something nobody has seen", "generic_decline"),
    ],
)
def test_refusal_summary_maps_into_taxonomy(summary: str, code: str) -> None:
    assert refusal_code(summary) == code
