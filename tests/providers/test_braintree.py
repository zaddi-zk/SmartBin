"""Braintree adapter contract (section 24)."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx
from app.config import Settings
from app.core.parse import Card
from app.core.taxonomy import classify
from app.core.verdicts import Verdict
from app.providers.base import Key
from app.providers.braintree import BraintreeAdapter, refusal_code

BASE = "https://api.braintreegateway.com"
MERCHANT = "testmerchant"
TRANSACTIONS = f"{BASE}/merchants/{MERCHANT}/transactions"
CARD = Card.parse("4242424242424242|11|29|716")


def settings() -> Settings:
    return Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        braintree_merchant_id=MERCHANT,
        braintree_public_key="public123",
        braintree_private_key="private456",
        braintree_currency="USD",
    )


def adapter() -> BraintreeAdapter:
    return BraintreeAdapter(settings())


def key(secret: str | None = None) -> Key:
    return Key(
        id=1,
        gate_id="charge_braintree",
        secret=secret or f"{MERCHANT}|public123|private456",
        country="US",
    )


def transaction(status: str = "AUTHORIZED", **fields: str) -> str:
    body = "".join(f"<{name}>{value}</{name}>" for name, value in fields.items())
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        f"<transaction><id>txn_1</id><status>{status}</status>{body}</transaction>"
    )


def declined(code: str, reason: str = "Declined") -> str:
    return transaction(
        "PROCESSOR_DECLINED",
        **{"processor-response-code": code, "processor-response-text": reason},
    )


@respx.mock
async def test_authorized_is_approved() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(
            201,
            text=transaction(
                **{"avs-error-response-code": "M", "cvv-error-response-code": "M"}
            ),
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert raw.status == 200
    assert raw.provider_ref == "txn_1"
    assert raw.avs == "M"
    assert raw.cvc == "M"
    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_submitted_for_settlement_is_approved() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(201, text=transaction("SUBMITTED_FOR_SETTLEMENT"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, True)

    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_insufficient_funds_is_live() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(201, text=declined("2001", "Insufficient Funds"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.LIVE, "insufficient_funds")


@respx.mock
async def test_numeric_code_wins_over_generic_text() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(201, text=declined("2001", "Declined"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.LIVE, "insufficient_funds")


@respx.mock
async def test_do_not_honor_is_dead() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(201, text=declined("2000", "Do Not Honor"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "do_not_honor")


@respx.mock
async def test_expired_card_is_dead() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(201, text=declined("2004", "Expired Card"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "expired_card")


@respx.mock
async def test_gateway_rejection_cvv_is_dead() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(
            201,
            text=transaction(
                "GATEWAY_REJECTED", **{"gateway-rejection-reason": "cvv"}
            ),
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "incorrect_cvc")


@respx.mock
async def test_gateway_rejection_avs_is_dead() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(
            201,
            text=transaction(
                "GATEWAY_REJECTED", **{"gateway-rejection-reason": "avs"}
            ),
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "incorrect_zip")


@respx.mock
async def test_declined_status_is_dead() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(201, text=transaction("DECLINED"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "generic_decline")


@respx.mock
async def test_failed_status_is_retry() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(201, text=transaction("FAILED"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert raw.status == 502
    assert classify(raw.status, raw.body)[0] is Verdict.RETRY


@respx.mock
async def test_401_is_key_dead() -> None:
    respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(
            401,
            text="<api-error-response><message>Invalid credentials</message></api-error-response>",
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body)[0] is Verdict.KEY_DEAD


@respx.mock
async def test_429_is_retry() -> None:
    respx.post(TRANSACTIONS).mock(return_value=httpx.Response(429, text=""))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_429")


@respx.mock
async def test_500_is_retry() -> None:
    respx.post(TRANSACTIONS).mock(return_value=httpx.Response(500, text=""))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_500")


@respx.mock
async def test_timeout_raises_so_executor_marks_inconclusive() -> None:
    respx.post(TRANSACTIONS).mock(side_effect=httpx.ConnectTimeout("no route"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPError):
            await adapter().auth(client, key(), CARD, 100, False)


@respx.mock
async def test_malformed_xml_is_retry_not_dead() -> None:
    respx.post(TRANSACTIONS).mock(return_value=httpx.Response(200, text="<html>proxy"))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert raw.status == 502
    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_502")


@respx.mock
async def test_auth_payload_shape_and_basic_auth() -> None:
    route = respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(201, text=transaction())
    )
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key(), CARD, 250, False)

    request = route.calls[0].request
    body = request.content.decode()
    assert "<amount>2.50</amount>" in body
    assert "<submit-for-settlement type=\"boolean\">false</submit-for-settlement>" in body
    assert "<expiration-date>11/2029</expiration-date>" in body
    assert "<cvv>716</cvv>" in body
    expected = base64.b64encode(b"public123:private456").decode()
    assert request.headers["Authorization"] == f"Basic {expected}"


@respx.mock
async def test_charge_submits_for_settlement() -> None:
    route = respx.post(TRANSACTIONS).mock(
        return_value=httpx.Response(201, text=transaction())
    )
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key(), CARD, 100, True)

    body = route.calls[0].request.content.decode()
    assert "<submit-for-settlement type=\"boolean\">true</submit-for-settlement>" in body


@respx.mock
async def test_capture_and_void_paths() -> None:
    capture = respx.put(f"{TRANSACTIONS}/txn_1/submit_for_settlement").mock(
        return_value=httpx.Response(
            200, text=transaction("SUBMITTED_FOR_SETTLEMENT")
        )
    )
    void = respx.put(f"{TRANSACTIONS}/txn_1/void").mock(
        return_value=httpx.Response(200, text=transaction("VOIDED"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().capture(client, key(), "txn_1", 500)
        await adapter().cancel(client, key(), "txn_1")
        await adapter().cancel(client, key(), "")

    assert raw.status == 200
    assert capture.called and void.call_count == 1
    assert "<amount>5.00</amount>" in capture.calls[0].request.content.decode()


@respx.mock
async def test_validate_key_uses_a_free_client_token() -> None:
    route = respx.post(f"{BASE}/merchants/{MERCHANT}/client_token").mock(
        return_value=httpx.Response(201, text="<client-token>token</client-token>")
    )
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is True
    assert route.called


@respx.mock
async def test_validate_key_rejects_401_and_network() -> None:
    respx.post(f"{BASE}/merchants/{MERCHANT}/client_token").mock(
        return_value=httpx.Response(401, text="")
    )
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False
    respx.post(f"{BASE}/merchants/{MERCHANT}/client_token").mock(
        side_effect=httpx.ConnectTimeout("no route")
    )
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False


@respx.mock
async def test_key_row_overrides_env_credentials() -> None:
    route = respx.post(f"{BASE}/merchants/other/transactions").mock(
        return_value=httpx.Response(201, text=transaction())
    )
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key("other|pub2|priv2"), CARD, 100, False)

    request = route.calls[0].request
    expected = base64.b64encode(b"pub2:priv2").decode()
    assert request.headers["Authorization"] == f"Basic {expected}"


@pytest.mark.parametrize(
    ("response_code", "reason", "code"),
    [
        ("2001", "", "insufficient_funds"),
        ("2000", "", "do_not_honor"),
        ("2005", "", "incorrect_number"),
        ("2004", "", "expired_card"),
        ("2036", "", "lost_card"),
        ("2037", "", "stolen_card"),
        ("2035", "", "restricted_card"),
        ("2040", "", "card_velocity_exceeded"),
        ("2017", "", "call_issuer"),
        ("2010", "", "incorrect_cvc"),
        ("2011", "", "incorrect_zip"),
        ("9999", "Insufficient funds", "insufficient_funds"),
        ("9999", "Expired card", "expired_card"),
        ("9999", "Fraud suspected", "fraudulent"),
        ("9999", "something nobody has seen", "generic_decline"),
    ],
)
def test_refusal_code_maps_into_taxonomy(
    response_code: str, reason: str, code: str
) -> None:
    assert refusal_code(response_code, reason) == code
