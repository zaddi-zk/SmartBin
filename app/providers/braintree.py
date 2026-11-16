"""Braintree adapter (section 9).

Braintree speaks XML over HTTP Basic auth. Auth is a ``sale`` with
``submit-for-settlement=false``; charge flips that option; capture is
``PUT /transactions/{id}/submit_for_settlement`` and cancel is
``PUT /transactions/{id}/void``. Verdicts stay in ``app.core.taxonomy``.
"""

from __future__ import annotations

from xml.etree import ElementTree

import httpx
from defusedxml.ElementTree import fromstring

from app.config import Settings
from app.core.parse import Card
from app.core.taxonomy import derive_decline_code
from app.providers.base import Key, RawResult

_APPROVED = frozenset({"AUTHORIZED", "SUBMITTED_FOR_SETTLEMENT", "SETTLING", "SETTLED"})

# Braintree processor response codes. The numeric code is authoritative; the
# text is only a fallback for codes this map has not seen yet.
_RESPONSE_CODES: dict[str, str] = {
    "2000": "do_not_honor",
    "2001": "insufficient_funds",
    "2002": "card_velocity_exceeded",
    "2003": "card_velocity_exceeded",
    "2004": "expired_card",
    "2005": "incorrect_number",
    "2006": "invalid_expiry_month",
    "2007": "no_account",
    "2008": "incorrect_number",
    "2009": "card_not_supported",
    "2010": "incorrect_cvc",
    "2011": "incorrect_zip",
    "2012": "generic_decline",
    "2014": "incorrect_cvc",
    "2015": "incorrect_cvc",
    "2017": "call_issuer",
    "2019": "generic_decline",
    "2020": "currency_not_supported",
    "2021": "card_not_supported",
    "2023": "transaction_not_allowed",
    "2028": "incorrect_number",
    "2029": "invalid_expiry_year",
    "2030": "incorrect_cvc",
    "2032": "currency_not_supported",
    "2033": "card_not_supported",
    "2035": "restricted_card",
    "2036": "lost_card",
    "2037": "stolen_card",
    "2038": "fraudulent",
    "2039": "transaction_not_allowed",
    "2040": "card_velocity_exceeded",
    "2041": "account_closed",
    "2042": "restricted_card",
    "2043": "no_account",
    "2044": "invalid_account",
}


def _text(root: ElementTree.Element, *names: str) -> str:
    for name in names:
        found = root.find(name)
        if found is not None and found.text:
            value = str(found.text).strip()
            if value:
                return value
        for element in root.iter(name):
            if element.text:
                value = str(element.text).strip()
                if value:
                    return value
    return ""


def refusal_code(response_code: str, reason: str = "") -> str:
    """Map Braintree's processor response code, then its text, onto taxonomy."""
    mapped = _RESPONSE_CODES.get(response_code)
    if mapped:
        return mapped
    return derive_decline_code(reason) or "generic_decline"


def _gateway_rejection(reason: str) -> str:
    lowered = reason.lower()
    if "cvv" in lowered:
        return "incorrect_cvc"
    if "avs" in lowered or "postal" in lowered or "address" in lowered:
        return "incorrect_zip"
    if "fraud" in lowered:
        return "fraudulent"
    return "generic_decline"


def _xml(text: str) -> ElementTree.Element | None:
    try:
        # defusedxml refuses entity-expansion attacks; it returns an Element.
        return fromstring(text)
    except ElementTree.ParseError:
        return None


class BraintreeAdapter:
    name = "braintree"

    def __init__(self, settings: Settings, *, base_url: str | None = None) -> None:
        self.version = "2024-01-01"
        self.base_url = (base_url or settings.braintree_api_base).rstrip("/")
        self.currency = settings.braintree_currency.upper()
        self.merchant_id = settings.braintree_merchant_id.get_secret_value()
        self.public_key = settings.braintree_public_key.get_secret_value()
        self.private_key = settings.braintree_private_key.get_secret_value()

    # --- plumbing -----------------------------------------------------------
    def _credentials(self, key: Key) -> tuple[str, str, str]:
        """A key row stores ``merchant_id|public_key|private_key``."""
        if key.secret.count("|") >= 2:
            merchant, _, rest = key.secret.partition("|")
            public, _, private = rest.partition("|")
            return merchant, public, private
        return self.merchant_id, self.public_key, self.private_key

    def _merchant(self, key: Key) -> str:
        merchant, _, _ = self._credentials(key)
        return merchant

    def _auth(self, key: Key) -> httpx.BasicAuth:
        _, public, private = self._credentials(key)
        return httpx.BasicAuth(public, private)

    async def _request(
        self,
        client: httpx.AsyncClient,
        key: Key,
        method: str,
        path: str,
        body: str = "",
    ) -> RawResult:
        response = await client.request(
            method,
            f"{self.base_url}/merchants/{self._merchant(key)}{path}",
            auth=self._auth(key),
            headers={
                "Content-Type": "application/xml",
                "Accept": "application/xml",
                "X-ApiVersion": "6",
            },
            content=body.encode() if body else None,
        )
        return self._map(response)

    def _card_xml(self, card: Card) -> str:
        return (
            "<credit-card>"
            f"<number>{card.number}</number>"
            f"<expiration-date>{card.exp_month:02d}/{card.exp_year:04d}</expiration-date>"
            f"<cvv>{card.cvv}</cvv>"
            "</credit-card>"
        )

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
        body = (
            "<transaction>"
            f"<amount>{amount:.2f}</amount>"
            f"{self._card_xml(card)}"
            "<options>"
            f'<submit-for-settlement type="boolean">{"true" if capture else "false"}'
            "</submit-for-settlement>"
            "</options>"
            "<transaction-source>api</transaction-source>"
            "<type>sale</type>"
            "</transaction>"
        )
        return await self._request(client, key, "POST", "/transactions", body)

    async def capture(
        self, client: httpx.AsyncClient, key: Key, ref: str, amount_cents: int
    ) -> RawResult:
        amount = (int(amount_cents) if amount_cents > 0 else 100) / 100
        body = f"<transaction><amount>{amount:.2f}</amount></transaction>"
        return await self._request(
            client, key, "PUT", f"/transactions/{ref}/submit_for_settlement", body
        )

    async def cancel(self, client: httpx.AsyncClient, key: Key, ref: str) -> None:
        if not ref:
            return
        try:
            await self._request(client, key, "PUT", f"/transactions/{ref}/void")
        except httpx.HTTPError:
            # Releasing an already-voided hold is not an error.
            return

    async def validate_key(self, client: httpx.AsyncClient, key: Key) -> bool:
        """A client token proves the credentials and moves no money."""
        try:
            response = await client.request(
                "POST",
                f"{self.base_url}/merchants/{self._merchant(key)}/client_token",
                auth=self._auth(key),
                headers={"Accept": "application/xml", "X-ApiVersion": "6"},
            )
        except httpx.HTTPError:
            return False
        return response.status_code in (200, 201)

    # --- mapping ------------------------------------------------------------
    def _map(self, response: httpx.Response) -> RawResult:
        if response.status_code in (401, 403):
            root = _xml(response.text)
            message = _text(root, "message") if root is not None else ""
            return RawResult(
                401,
                {
                    "error": {
                        "code": "invalid_api_key",
                        "message": message or "unauthorized",
                    }
                },
            )
        if response.status_code == 429:
            return RawResult(429, {"error": {"code": "http_429"}})
        if response.status_code >= 500:
            return RawResult(
                response.status_code,
                {"error": {"code": f"http_{response.status_code}"}},
            )

        root = _xml(response.text)
        if root is None:
            return RawResult(
                502,
                {"error": {"code": "malformed_response", "message": response.text[:200]}},
            )
        if response.status_code >= 400:
            return RawResult(
                response.status_code,
                {
                    "error": {
                        "code": f"http_{response.status_code}",
                        "message": _text(root, "message"),
                    }
                },
            )

        if root.tag == "api-error-response":
            message = _text(root, "message") or "gateway rejected"
            return RawResult(
                502, {"error": {"code": "gateway_rejected", "message": message}}
            )

        ref = _text(root, "id")
        status = _text(root, "status").upper()
        avs = _text(root, "avs-error-response-code")
        cvc = _text(root, "cvv-error-response-code")

        if status in _APPROVED:
            return RawResult(200, {}, provider_ref=ref, avs=avs, cvc=cvc, three_ds="N")

        if status == "PROCESSOR_DECLINED":
            code = _text(root, "processor-response-code")
            reason = _text(root, "processor-response-text") or "declined"
            return RawResult(
                402,
                {"error": {"code": refusal_code(code, reason), "message": reason}},
                provider_ref=ref,
                avs=avs,
                cvc=cvc,
            )

        if status == "GATEWAY_REJECTED":
            reason = _text(root, "gateway-rejection-reason") or "gateway rejected"
            return RawResult(
                402,
                {"error": {"code": _gateway_rejection(reason), "message": reason}},
                provider_ref=ref,
                avs=avs,
                cvc=cvc,
            )

        if status == "DECLINED":
            return RawResult(
                402,
                {"error": {"code": "generic_decline", "message": "declined"}},
                provider_ref=ref,
                avs=avs,
                cvc=cvc,
            )

        if status in ("FAILED", "GATEWAY_REJECTED_ERROR"):
            return RawResult(502, {}, provider_ref=ref)

        # Anything else is not terminal: no credit moves.
        return RawResult(202, {}, provider_ref=ref, avs=avs, cvc=cvc)
