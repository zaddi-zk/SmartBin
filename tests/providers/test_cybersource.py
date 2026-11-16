"""CyberSource adapter contract (section 24)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
import respx
from app.config import Settings
from app.core.parse import Card
from app.core.taxonomy import classify
from app.core.verdicts import Verdict
from app.providers.base import Key
from app.providers.cybersource import CybersourceAdapter, refusal_code

BASE = "https://api.cybersource.com"
PAYMENTS = f"{BASE}/pts/v2/payments"
CARD = Card.parse("4242424242424242|11|29|716")
MERCHANT = "testmerchant"
KEY_ID = "key123"
SHARED = "shared-secret"


def settings() -> Settings:
    return Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        cybersource_merchant_id=MERCHANT,
        cybersource_key_id=KEY_ID,
        cybersource_shared_secret=SHARED,
        cybersource_currency="USD",
    )


def adapter() -> CybersourceAdapter:
    return CybersourceAdapter(settings())


def key(secret: str | None = None) -> Key:
    return Key(
        id=1,
        gate_id="charge_cybersource",
        secret=secret or f"{MERCHANT}|{KEY_ID}|{SHARED}",
        country="US",
    )


def payment(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "pay_1",
        "status": "AUTHORIZED",
        "processorInformation": {
            "responseCode": "00",
            "avsCode": "Y",
            "cvCode": "M",
        },
    }
    base.update(overrides)
    return base


def declined(code: str, message: str = "Declined") -> dict[str, object]:
    return payment(
        status="DECLINED",
        processorInformation={"responseCode": code},
        errorInformation={"reason": "DECLINED", "message": message},
    )


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
        return_value=httpx.Response(201, json=payment(status="CAPTURED"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, True)

    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_insufficient_funds_is_live() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(201, json=declined("51", "Insufficient funds"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.LIVE, "insufficient_funds")


@respx.mock
async def test_numeric_code_wins_over_generic_reason() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(201, json=declined("51", "The card was declined"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.LIVE, "insufficient_funds")


@respx.mock
async def test_do_not_honor_is_dead() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(201, json=declined("05", "Do not honor"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "do_not_honor")


@respx.mock
async def test_expired_card_is_dead() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(201, json=declined("54", "Expired card"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "expired_card")


@respx.mock
async def test_held_for_review_is_live() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            201, json=payment(status="AUTHORIZED_PENDING_REVIEW")
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body) == (Verdict.LIVE, "approve_with_id")


@respx.mock
async def test_3ds_challenge_carries_acs_url() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            201,
            json=payment(
                status="PENDING_AUTHENTICATION",
                consumerAuthenticationInformation={
                    "acsUrl": "https://acs.example/challenge",
                    "accessToken": "token",
                },
            ),
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert raw.status == 402
    assert raw.acs_url == "https://acs.example/challenge"
    assert classify(raw.status, raw.body) == (Verdict.LIVE_3DS, "authentication_required")


@respx.mock
async def test_401_is_key_dead() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            401,
            json={
                "status": "INVALID_REQUEST",
                "errorInformation": {
                    "reason": "AUTHENTICATION_FAILED",
                    "message": "Authentication failed",
                },
            },
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert classify(raw.status, raw.body)[0] is Verdict.KEY_DEAD


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
async def test_server_error_status_is_retry() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(201, json=payment(status="SERVER_ERROR"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 100, False)

    assert raw.status == 502
    assert classify(raw.status, raw.body)[0] is Verdict.RETRY


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
async def test_auth_payload_shape_and_capture_flag() -> None:
    route = respx.post(PAYMENTS).mock(return_value=httpx.Response(201, json=payment()))
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key(), CARD, 250, False)

    payload = json.loads(route.calls[0].request.content)
    assert payload["processingInformation"]["capture"] is False
    assert payload["orderInformation"]["amountDetails"]["totalAmount"] == "2.50"
    assert payload["orderInformation"]["amountDetails"]["currency"] == "USD"
    card = payload["paymentInformation"]["card"]
    assert card["number"] == "4242424242424242"
    assert card["expirationMonth"] == "11"
    assert card["expirationYear"] == "2029"
    assert card["securityCode"] == "716"


def _signature_valid(request: httpx.Request, shared: str) -> bool:
    headers = request.headers
    signed = (
        f"host: {headers['Host']}\n"
        f"date: {headers['Date']}\n"
        f"(request-target): {request.method.lower()} {request.url.path}\n"
        f"digest: {headers['Digest']}\n"
        f"v-c-merchant-id: {headers['v-c-merchant-id']}"
    )
    expected = base64.b64encode(
        hmac.new(shared.encode(), signed.encode(), hashlib.sha256).digest()
    ).decode()
    return f'signature="{expected}"' in headers["Signature"]


@respx.mock
async def test_request_is_signed_with_the_shared_secret() -> None:
    route = respx.post(PAYMENTS).mock(return_value=httpx.Response(201, json=payment()))
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key(), CARD, 100, False)

    request = route.calls[0].request
    assert _signature_valid(request, SHARED)
    assert f'keyid="{KEY_ID}"' in request.headers["Signature"]
    assert request.headers["v-c-merchant-id"] == MERCHANT
    digest = base64.b64encode(hashlib.sha256(request.content).digest()).decode()
    assert request.headers["Digest"] == f"SHA-256={digest}"


@respx.mock
async def test_key_row_overrides_env_credentials() -> None:
    route = respx.post(PAYMENTS).mock(return_value=httpx.Response(201, json=payment()))
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key("other|kid2|secret2"), CARD, 100, False)

    request = route.calls[0].request
    assert request.headers["v-c-merchant-id"] == "other"
    assert 'keyid="kid2"' in request.headers["Signature"]
    assert _signature_valid(request, "secret2")


@respx.mock
async def test_capture_and_void_paths() -> None:
    capture = respx.post(f"{PAYMENTS}/pay_1/captures").mock(
        return_value=httpx.Response(201, json=payment(status="CAPTURED"))
    )
    void = respx.post(f"{PAYMENTS}/pay_1/voids").mock(
        return_value=httpx.Response(201, json=payment(status="VOIDED"))
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().capture(client, key(), "pay_1", 500)
        await adapter().cancel(client, key(), "pay_1")
        await adapter().cancel(client, key(), "")

    assert raw.status == 200
    assert capture.called and void.call_count == 1
    payload = json.loads(capture.calls[0].request.content)
    assert payload["orderInformation"]["amountDetails"]["totalAmount"] == "5.00"


@respx.mock
async def test_validate_key_accepts_a_400_for_a_valid_signature() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            400,
            json={
                "status": "INVALID_REQUEST",
                "errorInformation": {"reason": "MISSING_FIELD"},
            },
        )
    )
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is True


@respx.mock
async def test_validate_key_rejects_401_and_network() -> None:
    respx.post(PAYMENTS).mock(return_value=httpx.Response(401, json={}))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False
    respx.post(PAYMENTS).mock(side_effect=httpx.ConnectTimeout("no route"))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False


@pytest.mark.parametrize(
    ("response_code", "reason", "code"),
    [
        ("51", "", "insufficient_funds"),
        ("05", "", "do_not_honor"),
        ("14", "", "incorrect_number"),
        ("54", "", "expired_card"),
        ("41", "", "lost_card"),
        ("43", "", "stolen_card"),
        ("62", "", "restricted_card"),
        ("57", "", "transaction_not_allowed"),
        ("61", "", "withdrawal_count_limit_exceeded"),
        ("65", "", "card_velocity_exceeded"),
        ("91", "", "issuer_unavailable"),
        ("96", "", "system_error"),
        ("99", "Insufficient funds", "insufficient_funds"),
        ("99", "Expired card", "expired_card"),
        ("99", "Fraud suspected", "fraudulent"),
        ("99", "something nobody has seen", "generic_decline"),
    ],
)
def test_refusal_code_maps_into_taxonomy(
    response_code: str, reason: str, code: str
) -> None:
    assert refusal_code(response_code, reason) == code
