"""Adyen adapter contract (section 24).

Same matrix as Stripe: approve, insufficient_funds, generic_decline,
authentication_required, 401, 429, 500, timeout, malformed body. The adapter
only reshapes; ``app.core.taxonomy`` decides the verdict.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from app.config import Settings
from app.core.parse import Card
from app.core.taxonomy import classify
from app.core.verdicts import Verdict
from app.providers.adyen import AdyenAdapter, refusal_code
from app.providers.base import Key

BASE = "https://checkout-live.adyen.com/v71"
PAYMENTS = f"{BASE}/payments"
METHODS = f"{BASE}/paymentMethods"

CARD = Card.parse("4242424242424242|11|29|716")


def settings() -> Settings:
    return Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        adyen_api_key="adyen_live_test",
        adyen_merchant_account="TestMerchant",
    )


def adapter() -> AdyenAdapter:
    return AdyenAdapter(settings())


def key() -> Key:
    return Key(id=1, gate_id="auth_adyen", secret="adyen_live_test", country="US")


@respx.mock
async def test_authorised_is_approved() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            200,
            json={
                "pspReference": "psp_1",
                "resultCode": "Authorised",
                "additionalData": {"avsResultRaw": "Y", "cvcResultRaw": "M"},
            },
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 200
    assert raw.provider_ref == "psp_1"
    assert raw.avs == "Y"
    assert raw.cvc == "M"
    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_insufficient_funds_is_live() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            200,
            json={
                "pspReference": "psp_2",
                "resultCode": "Refused",
                "refusalReason": "Insufficient funds",
            },
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 402
    assert classify(raw.status, raw.body) == (Verdict.LIVE, "insufficient_funds")


@respx.mock
async def test_plain_refusal_is_dead() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            200,
            json={"pspReference": "psp_3", "resultCode": "Refused", "refusalReason": "Refused"},
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body) == (Verdict.DEAD, "generic_decline")


@respx.mock
async def test_redirect_shopper_carries_acs_url() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            200,
            json={
                "pspReference": "psp_4",
                "resultCode": "RedirectShopper",
                "action": {"type": "redirect", "url": "https://acs.example/challenge"},
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
async def test_pending_is_retry_not_approved() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            200, json={"pspReference": "psp_5", "resultCode": "Pending"}
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 202
    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_202")


@respx.mock
async def test_401_is_key_dead() -> None:
    respx.post(PAYMENTS).mock(
        return_value=httpx.Response(
            401,
            json={"status": 401, "errorCode": "000", "message": "Unauthorized"},
        )
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body)[0] is Verdict.KEY_DEAD


@respx.mock
async def test_429_is_retry() -> None:
    respx.post(PAYMENTS).mock(return_value=httpx.Response(429, json={"message": "rate"}))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_429")


@respx.mock
async def test_500_is_retry() -> None:
    respx.post(PAYMENTS).mock(return_value=httpx.Response(500, json={"message": "boom"}))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_500")


@respx.mock
async def test_timeout_raises_so_executor_marks_inconclusive() -> None:
    respx.post(PAYMENTS).mock(side_effect=httpx.ConnectTimeout("no route"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPError):
            await adapter().auth(client, key(), CARD, 0, False)


@respx.mock
async def test_malformed_200_is_retry_not_dead() -> None:
    respx.post(PAYMENTS).mock(return_value=httpx.Response(200, text="<html>proxy</html>"))
    async with httpx.AsyncClient() as client:
        raw = await adapter().auth(client, key(), CARD, 0, False)

    assert raw.status == 502
    assert classify(raw.status, raw.body) == (Verdict.RETRY, "http_502")


@respx.mock
async def test_auth_uses_delayed_capture_and_merchant() -> None:
    route = respx.post(PAYMENTS).mock(
        return_value=httpx.Response(200, json={"pspReference": "psp_6", "resultCode": "Authorised"})
    )
    async with httpx.AsyncClient() as client:
        await adapter().auth(client, key(), CARD, 250, True)

    body = route.calls[0].request.content.decode()
    assert "TestMerchant" in body
    assert '"captureDelayHours":7' in body
    assert '"value":250' in body


@respx.mock
async def test_capture_posts_to_captures() -> None:
    route = respx.post(f"{PAYMENTS}/psp_7/captures").mock(
        return_value=httpx.Response(200, json={"pspReference": "psp_7", "resultCode": "Authorised"})
    )
    async with httpx.AsyncClient() as client:
        raw = await adapter().capture(client, key(), "psp_7", 500)

    assert route.called
    assert classify(raw.status, raw.body) == (Verdict.APPROVED, "ok")


@respx.mock
async def test_cancel_posts_to_cancels_and_ignores_empty_ref() -> None:
    route = respx.post(f"{PAYMENTS}/psp_8/cancels").mock(
        return_value=httpx.Response(201, json={})
    )
    async with httpx.AsyncClient() as client:
        await adapter().cancel(client, key(), "psp_8")
        await adapter().cancel(client, key(), "")
    assert route.call_count == 1


@respx.mock
async def test_validate_key_uses_free_payment_methods_read() -> None:
    route = respx.post(METHODS).mock(return_value=httpx.Response(200, json={"paymentMethods": []}))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is True
    assert route.called


@respx.mock
async def test_validate_key_rejects_401_and_network_error() -> None:
    respx.post(METHODS).mock(return_value=httpx.Response(401, json={"message": "no"}))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False
    respx.post(METHODS).mock(side_effect=httpx.ConnectTimeout("no route"))
    async with httpx.AsyncClient() as client:
        assert await adapter().validate_key(client, key()) is False


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("Insufficient funds", "insufficient_funds"),
        ("Not enough balance", "insufficient_funds"),
        ("Card expired", "expired_card"),
        ("Invalid card number", "incorrect_number"),
        ("CVC Declined", "incorrect_cvc"),
        ("3D Not Authenticated", "authentication_not_handled"),
        ("Transaction Not Permitted", "transaction_not_allowed"),
        ("Restricted Card", "restricted_card"),
        ("Lost Card", "lost_card"),
        ("Stolen Card", "stolen_card"),
        ("Fraud", "fraudulent"),
        ("Acquirer Error", "acquirer_error"),
        ("Issuer Unavailable", "issuer_unavailable"),
        ("Refused", "generic_decline"),
        ("something nobody has seen", "generic_decline"),
    ],
)
def test_refusal_reason_maps_into_taxonomy(reason: str, code: str) -> None:
    assert refusal_code(reason) == code
