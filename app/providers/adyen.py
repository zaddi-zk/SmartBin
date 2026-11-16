"""Adyen adapter (section 9).

Live Checkout API. Auth is a ``/payments`` call with a delayed capture window;
nothing settles until the executor captures or cancels. Adyen reports the
outcome in ``resultCode`` and the reason in free-text ``refusalReason``, so the
adapter derives a taxonomy code from that text and keeps the raw reason in the
message. Verdicts stay in ``app.core.taxonomy``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx

from app.config import Settings
from app.core.parse import Card
from app.providers.base import Key, RawResult

_THREE_DS_RESULTS = frozenset({"RedirectShopper", "ChallengeShopper", "IdentifyShopper"})
_PENDING_RESULTS = frozenset({"Received", "Pending"})

# Adyen has no decline_code field. Map the human reason onto the shared
# taxonomy. Order matters: "insufficient funds" must win over "refused".
_REFUSAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"insufficient|not enough (?:funds|balance)|not_sufficient", re.I),
        "insufficient_funds",
    ),
    (re.compile(r"expired", re.I), "expired_card"),
    (
        re.compile(r"invalid (?:card )?number|invalid account|no account", re.I),
        "incorrect_number",
    ),
    (re.compile(r"\bcvc\b|\bcvv\b", re.I), "incorrect_cvc"),
    (re.compile(r"3d|authenticat", re.I), "authentication_not_handled"),
    (
        re.compile(r"not permitted|transaction not allowed|not allowed", re.I),
        "transaction_not_allowed",
    ),
    (re.compile(r"restricted|blocked", re.I), "restricted_card"),
    (re.compile(r"\blost\b", re.I), "lost_card"),
    (re.compile(r"stolen", re.I), "stolen_card"),
    (re.compile(r"fraud", re.I), "fraudulent"),
    (re.compile(r"acquirer error|system error", re.I), "acquirer_error"),
    (re.compile(r"issuer (?:unavailable|not available)", re.I), "issuer_unavailable"),
    (re.compile(r"refused|declined", re.I), "generic_decline"),
)


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"error": {"code": "malformed_response", "message": response.text[:200]}}
    return payload if isinstance(payload, dict) else {"error": {"code": "malformed_response"}}


def refusal_code(reason: str) -> str:
    """Derive a taxonomy code from Adyen's free-text refusal reason."""
    for pattern, code in _REFUSAL_PATTERNS:
        if pattern.search(reason):
            return code
    return "generic_decline"


def _action_url(action: Mapping[str, Any]) -> str:
    redirect = _as_mapping(action.get("redirect"))
    for candidate in (redirect.get("url"), action.get("url"), action.get("redirectUrl")):
        if candidate:
            return str(candidate)
    return ""


class AdyenAdapter:
    name = "adyen"

    def __init__(self, settings: Settings, *, base_url: str | None = None) -> None:
        self.version = "v71"
        self.base_url = (base_url or settings.adyen_api_base).rstrip("/")
        self.merchant = settings.adyen_merchant_account
        self.currency = settings.adyen_currency.upper()
        self.capture_delay_hours = settings.adyen_capture_delay_hours

    # --- plumbing -----------------------------------------------------------
    def _headers(self, key: Key) -> dict[str, str]:
        return {
            "x-API-key": key.secret,
            "Idempotency-Key": str(uuid4()),
            "Content-Type": "application/json",
        }

    def _amount(self, amount_cents: int) -> dict[str, object]:
        return {"value": max(0, int(amount_cents)), "currency": self.currency}

    def _card_payload(self, card: Card) -> dict[str, object]:
        payment_method: dict[str, object] = {
            "type": "scheme",
            "number": card.number,
            "expiryMonth": f"{card.exp_month:02d}",
            "expiryYear": str(card.exp_year),
            "cvc": card.cvv,
        }
        if card.holder:
            payment_method["holderName"] = card.holder
        return payment_method

    # --- adapter contract ---------------------------------------------------
    async def auth(
        self,
        client: httpx.AsyncClient,
        key: Key,
        card: Card,
        amount_cents: int,
        capture: bool,
    ) -> RawResult:
        payload: dict[str, object] = {
            "amount": self._amount(amount_cents),
            "paymentMethod": self._card_payload(card),
            "reference": f"sb-{uuid4().hex[:24]}",
            "merchantAccount": self.merchant,
            # Delayed capture keeps every lane an authorisation: the executor
            # captures (charge) or cancels (kill/auth) explicitly.
            "captureDelayHours": self.capture_delay_hours,
        }
        if card.postal or card.country:
            payload["billingAddress"] = {
                "postalCode": card.postal or "",
                "country": card.country or "US",
            }
        response = await client.post(
            f"{self.base_url}/payments", headers=self._headers(key), json=payload
        )
        return self._map_payment(response)

    async def cancel(self, client: httpx.AsyncClient, key: Key, ref: str) -> None:
        if not ref:
            return
        try:
            await client.post(
                f"{self.base_url}/payments/{ref}/cancels",
                headers=self._headers(key),
                json={
                    "merchantAccount": self.merchant,
                    "reference": f"cancel-{ref}",
                },
            )
        except httpx.HTTPError:
            # Cancelling an already-released hold is not an error worth raising.
            return

    async def capture(
        self, client: httpx.AsyncClient, key: Key, ref: str, amount_cents: int
    ) -> RawResult:
        response = await client.post(
            f"{self.base_url}/payments/{ref}/captures",
            headers=self._headers(key),
            json={
                "merchantAccount": self.merchant,
                "amount": self._amount(amount_cents),
                "reference": f"capture-{ref}",
            },
        )
        return self._map_payment(response)

    async def validate_key(self, client: httpx.AsyncClient, key: Key) -> bool:
        """A free ``/paymentMethods`` read. The live host is the livemode gate."""
        try:
            response = await client.post(
                f"{self.base_url}/paymentMethods",
                headers=self._headers(key),
                json={
                    "merchantAccount": self.merchant,
                    "amount": self._amount(100),
                },
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    # --- mapping ------------------------------------------------------------
    def _map_payment(self, response: httpx.Response) -> RawResult:
        body = _json(response)
        ref = str(body.get("pspReference") or "")
        if response.status_code >= 400:
            return RawResult(response.status_code, body, provider_ref=ref)

        if _as_mapping(body.get("error")):
            # A 2xx with an error envelope is a transport/proxy fault, not a
            # card verdict. RETRY keeps the credit intact.
            return RawResult(502, body, provider_ref=ref)

        result = str(body.get("resultCode") or "")
        if result in _THREE_DS_RESULTS:
            action = _as_mapping(body.get("action"))
            return RawResult(
                402,
                {"error": {"code": "authentication_required", "message": result}},
                provider_ref=ref,
                acs_url=_action_url(action),
                three_ds="C",
            )

        if result == "Authorised":
            extra = _as_mapping(body.get("additionalData"))
            return RawResult(
                200,
                body,
                provider_ref=ref,
                avs=str(extra.get("avsResultRaw") or extra.get("avsResult") or ""),
                cvc=str(extra.get("cvcResultRaw") or extra.get("cvcResult") or ""),
                three_ds="N",
            )

        if result in _PENDING_RESULTS:
            # Not terminal: the taxonomy maps 202 to RETRY, so no credit moves.
            return RawResult(202, body, provider_ref=ref)

        if result == "Refused":
            reason = str(body.get("refusalReason") or "Refused")
            return RawResult(
                402,
                {"error": {"code": refusal_code(reason), "message": reason}},
                provider_ref=ref,
            )

        return RawResult(
            502,
            {"error": {"code": "acquirer_error", "message": result or "unknown"}},
            provider_ref=ref,
        )
