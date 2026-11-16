"""CyberSource adapter (section 9).

CyberSource authenticates every call with an HTTP signature: the request
headers are signed with HMAC-SHA256 using the merchant's shared secret. Auth
uses ``/pts/v2/payments`` with ``processingInformation.capture=false``; charge
flips that flag; kill captures and voids by payment id. Verdicts stay in
``app.core.taxonomy``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from email.utils import formatdate
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.config import Settings
from app.core.parse import Card
from app.core.taxonomy import derive_decline_code
from app.providers.base import Key, RawResult

_APPROVED = frozenset({"AUTHORIZED", "CAPTURED", "PARTIAL_AUTHORIZED"})

# CyberSource processorInformation.responseCode. The numeric code is the most
# specific signal, so it wins over the free-text reason.
_RESPONSE_CODES: dict[str, str] = {
    "00": "ok",
    "04": "pickup_card",
    "05": "do_not_honor",
    "07": "pickup_card",
    "12": "generic_decline",
    "13": "generic_decline",
    "14": "incorrect_number",
    "15": "card_not_supported",
    "19": "reenter_transaction",
    "33": "expired_card",
    "34": "fraudulent",
    "41": "lost_card",
    "43": "stolen_card",
    "51": "insufficient_funds",
    "54": "expired_card",
    "57": "transaction_not_allowed",
    "58": "transaction_not_allowed",
    "59": "fraudulent",
    "61": "withdrawal_count_limit_exceeded",
    "62": "restricted_card",
    "63": "security_violation",
    "65": "card_velocity_exceeded",
    "78": "no_account",
    "91": "issuer_unavailable",
    "93": "generic_decline",
    "96": "system_error",
    "98": "invalid_account",
}


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json(response: httpx.Response) -> dict[str, Any]:
    text = response.text.strip()
    if not text:
        return {"error": {"code": "malformed_response", "message": "empty body"}}
    try:
        payload = json.loads(text)
    except ValueError:
        return {"error": {"code": "malformed_response", "message": text[:200]}}
    return payload if isinstance(payload, dict) else {"error": {"code": "malformed_response"}}


def refusal_code(response_code: str, reason: str = "") -> str:
    """Map CyberSource's numeric response code, then its text, onto taxonomy."""
    mapped = _RESPONSE_CODES.get(response_code)
    if mapped and mapped != "ok":
        return mapped
    return derive_decline_code(reason) or "generic_decline"


def _error_text(body: Mapping[str, Any]) -> str:
    error = _as_mapping(body.get("errorInformation"))
    return str(error.get("message") or error.get("reason") or "")


class CybersourceAdapter:
    name = "cybersource"

    def __init__(self, settings: Settings, *, base_url: str | None = None) -> None:
        self.version = "2024-01-01"
        self.base_url = (base_url or settings.cybersource_api_base).rstrip("/")
        self.currency = settings.cybersource_currency.upper()
        self.merchant_id = settings.cybersource_merchant_id.get_secret_value()
        self.key_id = settings.cybersource_key_id.get_secret_value()
        self.shared_secret = settings.cybersource_shared_secret.get_secret_value()
        self.host = urlparse(self.base_url).netloc

    # --- plumbing -----------------------------------------------------------
    def _credentials(self, key: Key) -> tuple[str, str, str]:
        """A key row stores ``merchant_id|key_id|shared_secret``."""
        if key.secret.count("|") >= 2:
            merchant, _, rest = key.secret.partition("|")
            key_id, _, shared = rest.partition("|")
            return merchant, key_id, shared
        return self.merchant_id, self.key_id, self.shared_secret

    def _headers(
        self, method: str, path: str, body: bytes, key: Key
    ) -> dict[str, str]:
        merchant, key_id, shared = self._credentials(key)
        date = formatdate(usegmt=True)
        digest = "SHA-256=" + base64.b64encode(hashlib.sha256(body).digest()).decode()
        signed = (
            f"host: {self.host}\n"
            f"date: {date}\n"
            f"(request-target): {method.lower()} {path}\n"
            f"digest: {digest}\n"
            f"v-c-merchant-id: {merchant}"
        )
        signature = base64.b64encode(
            hmac.new(shared.encode(), signed.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "Host": self.host,
            "Date": date,
            "Digest": digest,
            "v-c-merchant-id": merchant,
            "Signature": (
                f'keyid="{key_id}", algorithm="HmacSHA256", '
                f'headers="host date (request-target) digest v-c-merchant-id", '
                f'signature="{signature}"'
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _post(
        self, client: httpx.AsyncClient, key: Key, path: str, payload: dict[str, object]
    ) -> RawResult:
        body = json.dumps(payload, separators=(",", ":")).encode()
        response = await client.post(
            f"{self.base_url}{path}", headers=self._headers("post", path, body, key), content=body
        )
        return self._map(response)

    def _card_payload(self, card: Card) -> dict[str, object]:
        payload: dict[str, object] = {
            "number": card.number,
            "expirationMonth": f"{card.exp_month:02d}",
            "expirationYear": f"{card.exp_year:04d}",
            "securityCode": card.cvv,
        }
        return payload

    # --- adapter contract ---------------------------------------------------
    async def auth(
        self,
        client: httpx.AsyncClient,
        key: Key,
        card: Card,
        amount_cents: int,
        capture: bool,
    ) -> RawResult:
        amount = (int(amount_cents) if amount_cents > 0 else 100) / 100
        payload: dict[str, object] = {
            "clientReferenceInformation": {"code": f"sb-{uuid4().hex[:20]}"},
            "processingInformation": {"capture": capture, "commerceIndicator": "internet"},
            "paymentInformation": {"card": self._card_payload(card)},
            "orderInformation": {
                "amountDetails": {
                    "totalAmount": f"{amount:.2f}",
                    "currency": self.currency,
                }
            },
        }
        if card.postal or card.country:
            order = payload["orderInformation"]
            assert isinstance(order, dict)
            order["billTo"] = {
                "postalCode": card.postal,
                "country": card.country or "US",
            }
        return await self._post(client, key, "/pts/v2/payments", payload)

    async def capture(
        self, client: httpx.AsyncClient, key: Key, ref: str, amount_cents: int
    ) -> RawResult:
        amount = (int(amount_cents) if amount_cents > 0 else 100) / 100
        return await self._post(
            client,
            key,
            f"/pts/v2/payments/{ref}/captures",
            {
                "clientReferenceInformation": {"code": f"cap-{ref}"},
                "orderInformation": {
                    "amountDetails": {
                        "totalAmount": f"{amount:.2f}",
                        "currency": self.currency,
                    }
                },
            },
        )

    async def cancel(self, client: httpx.AsyncClient, key: Key, ref: str) -> None:
        if not ref:
            return
        try:
            await self._post(
                client,
                key,
                f"/pts/v2/payments/{ref}/voids",
                {"clientReferenceInformation": {"code": f"void-{ref}"}},
            )
        except httpx.HTTPError:
            # Releasing an already-voided hold is not an error.
            return

    async def validate_key(self, client: httpx.AsyncClient, key: Key) -> bool:
        """A deliberately incomplete payment: 400 means the signature is valid.

        A bad key or signature is rejected with 401/403 before the body is
        validated, so this costs nothing and never authorises a card.
        """
        payload: dict[str, object] = {
            "clientReferenceInformation": {"code": "sb-key-check"}
        }
        try:
            result = await self._post(client, key, "/pts/v2/payments", payload)
        except httpx.HTTPError:
            return False
        return result.status in (200, 201, 400)

    # --- mapping ------------------------------------------------------------
    def _map(self, response: httpx.Response) -> RawResult:
        body = _json(response)
        ref = str(body.get("id") or "")
        processor = _as_mapping(body.get("processorInformation"))
        avs = str(processor.get("avsCode") or "")
        cvc = str(processor.get("cvCode") or "")
        status = str(body.get("status") or "").upper()

        if response.status_code in (401, 403):
            return RawResult(
                401,
                {"error": {"code": "invalid_api_key", "message": _error_text(body)}},
                provider_ref=ref,
            )
        if response.status_code >= 400:
            return RawResult(response.status_code, body, provider_ref=ref)

        if _as_mapping(body.get("error")):
            # A 2xx with an error envelope is a transport/proxy fault.
            return RawResult(502, body, provider_ref=ref)

        if status in _APPROVED:
            return RawResult(200, body, provider_ref=ref, avs=avs, cvc=cvc, three_ds="N")

        if status == "AUTHORIZED_PENDING_REVIEW":
            return RawResult(
                402,
                {"error": {"code": "approve_with_id", "message": "held for review"}},
                provider_ref=ref,
                avs=avs,
                cvc=cvc,
            )

        if status == "PENDING_AUTHENTICATION":
            auth = _as_mapping(body.get("consumerAuthenticationInformation"))
            acs = str(auth.get("acsUrl") or auth.get("stepUpUrl") or "")
            return RawResult(
                402,
                {"error": {"code": "authentication_required", "message": "3DS challenge"}},
                provider_ref=ref,
                acs_url=acs,
                three_ds="C",
                avs=avs,
                cvc=cvc,
            )

        if status == "DECLINED":
            code = str(processor.get("responseCode") or "")
            reason = _error_text(body) or "declined"
            return RawResult(
                402,
                {"error": {"code": refusal_code(code, reason), "message": reason}},
                provider_ref=ref,
                avs=avs,
                cvc=cvc,
            )

        if status in ("SERVER_ERROR", "INVALID_REQUEST", "SYSTEM_ERROR"):
            # A malformed request is a platform fault, not a card verdict.
            return RawResult(502, body, provider_ref=ref)

        # Anything else is not terminal: no credit moves.
        return RawResult(202, body, provider_ref=ref, avs=avs, cvc=cvc)
