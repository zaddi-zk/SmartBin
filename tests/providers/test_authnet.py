"""Authorize.Net adapter contract (section 24).

The full matrix: approve, insufficient_funds, expired, generic_decline, held,
401, key-dead message, 429, 500, timeout, malformed body. The adapter only
reshapes; ``app.core.taxonomy`` decides the verdict.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from app.config import Settings
from app.core.parse import Card
from app.core.taxonomy import classify
from app.core.verdicts import Verdict
from app.providers.authnet import AuthNetAdapter, refusal_code
from app.providers.base import Key

URL = "https://api.authorize.net/xml/v1/request.api"
CARD = Card.parse("4242424242424242|11|29|716")


def settings() -> Settings:
    return Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        authnet_login_id="login123",
        authnet_transaction_key="txnkey456",
    )


def adapter() -> AuthNetAdapter:
    return AuthNetAdapter(settings())


def key() -> Key:
    return Key(
        id=1,
        gate_id="auth_authnet",
        secret="login123|txnkey456",
        country="US",
    )


def body(**transaction: object) -> dict[str, object]:
    return {
        "transactionResponse": {"transId": "12345", **transaction},
        "messages": {"resultCode": "Ok", "message": [{"code": "I00001", "text": "Successful."}]},
    }


def reply(payload: dict[str, object], status: int = 200) -> httpx.Response:
    # Authorize.Net prefixes JSON with a UTF-8 BOM.
    return httpx.Response(status, text="\ufeff" + json.dumps(payload))


@respx.mock
async def test_approved_is_terminal_ok() -> None:
    respx.post(URL).mock(
        return_value=reply(
            body(responseCode="1", avsResultCode="Y", cvvResultCode="M", authCode="ABC")
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 200
    assert raw.provider_ref == "12345"
    assert raw.avs == "Y"
    assert raw.cvc == "M"
    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_insufficient_funds_is_live() -> None:
    respx.post(URL).mock(
        return_value=reply(
            body(
                responseCode="2",
                errors=[{"errorCode": "2", "errorText": "Insufficient funds"}],
            )
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.LIVE, "insufficient_funds")


@respx.mock
async def test_expired_card_is_dead() -> None:
    respx.post(URL).mock(
        return_value=reply(
            body(
                responseCode="2",
                errors=[{"errorCode": "2", "errorText": "The credit card has expired."}],
            )
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "expired_card")


@respx.mock
async def test_generic_decline_is_dead() -> None:
    respx.post(URL).mock(
        return_value=reply(
            body(
                responseCode="2",
                errors=[{"errorCode": "2", "errorText": "This transaction has been declined."}],
            )
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "generic_decline")


@respx.mock
async def test_held_for_review_is_live() -> None:
    respx.post(URL).mock(
        return_value=reply(
            body(
                responseCode="4",
                errors=[{"errorCode": "4", "errorText": "Held for review"}],
            )
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.LIVE, "approve_with_id")


@respx.mock
async def test_http_401_is_key_dead() -> None:
    respx.post(URL).mock(return_value=httpx.Response(401, json={}))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body)[0] is Verdict.KEY_DEAD


@respx.mock
async def test_key_dead_message_is_401() -> None:
    respx.post(URL).mock(
        return_value=reply(
            {
                "messages": {
                    "resultCode": "Error",
                    "message": [{"code": "E00007", "text": "User authentication failed"}],
                }
            }
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 401
    assert classify(raw.status, raw.body)[0] is Verdict.KEY_DEAD


@respx.mock
async def test_429_is_retry() -> None:
    respx.post(URL).mock(return_value=httpx.Response(429, json={}))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_429")


@respx.mock
async def test_500_is_retry() -> None:
    respx.post(URL).mock(return_value=httpx.Response(500, json={}))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_500")


@respx.mock
async def test_timeout_raises_so_executor_marks_inconclusive() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("no route"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPError):
            await adapter().auth(client, key(), CARD, 0, False)


@respx.mock
async def test_malformed_200_is_retry_not_dead() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, text="<html>proxy</html>"))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 502
    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_502")


@respx.mock
async def test_auth_uses_auth_only_and_one_dollar_for_zero_amount() -> None:
    route = respx.post(URL).mock(return_value=reply(body(responseCode="1")))
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key(), CARD, 0, False)

    payload = json.loads(route.calls[0].request.content.decode().lstrip("\ufeff"))
    transaction = payload["createTransactionRequest"]["transactionRequest"]
    assert transaction["transactionType"] == "authOnlyTransaction"
    assert transaction["amount"] == "1.00"
    assert transaction["payment"]["creditCard"]["expirationDate"] == "2029-11"


@respx.mock
async def test_capture_uses_prior_auth_capture() -> None:
    route = respx.post(URL).mock(return_value=reply(body(responseCode="1")))
    async with httpx.AsyncClient() as client:
        await adapter().capture(client, key(), "12345", 500)

    payload = json.loads(route.calls[0].request.content.decode().lstrip("\ufeff"))
    transaction = payload["createTransactionRequest"]["transactionRequest"]
    assert transaction["transactionType"] == "priorAuthCaptureTransaction"
    assert transaction["refTransId"] == "12345"
    assert transaction["amount"] == "5.00"


@respx.mock
async def test_cancel_voids_and_ignores_empty_ref() -> None:
    route = respx.post(URL).mock(return_value=reply(body(responseCode="1")))
    async with httpx.AsyncClient() as client:
        await adapter().cancel(client, key(), "12345")
        await adapter().cancel(client, key(), "")

    assert route.call_count == 1
    payload = json.loads(route.calls[0].request.content.decode().lstrip("\ufeff"))
    assert payload["createTransactionRequest"]["transactionRequest"]["transactionType"] == (
        "voidTransaction"
    )


@respx.mock
async def test_validate_key_uses_authenticate_test() -> None:
    route = respx.post(URL).mock(
        return_value=reply(
            {"messages": {"resultCode": "Ok", "message": [{"code": "I00001", "text": "Ok"}]}}
        )
    )
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is True

    payload = json.loads(route.calls[0].request.content.decode().lstrip("\ufeff"))
    assert "authenticateTestRequest" in payload


@respx.mock
async def test_validate_key_rejects_bad_credentials_and_network() -> None:
    respx.post(URL).mock(return_value=httpx.Response(401, json={}))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("no route"))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("Insufficient funds", "insufficient_funds"),
        ("The credit card has expired.", "expired_card"),
        ("The credit card number is invalid.", "incorrect_number"),
        ("The card code is invalid.", "incorrect_cvc"),
        ("Transaction not allowed", "transaction_not_allowed"),
        ("Restricted card", "restricted_card"),
        ("Lost card", "lost_card"),
        ("Stolen card", "stolen_card"),
        ("Fraud suspected", "fraudulent"),
        ("Acquirer error", "acquirer_error"),
        ("Issuer unavailable", "issuer_unavailable"),
        ("Duplicate transaction", "duplicate_transaction"),
        ("This transaction has been declined.", "generic_decline"),
        ("something nobody has seen", "generic_decline"),
    ],
)
def test_refusal_reason_maps_into_taxonomy(reason: str, code: str) -> None:
    assert refusal_code(reason) == code
