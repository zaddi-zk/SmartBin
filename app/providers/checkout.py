"""Checkout.com adapter (section 9).

Live Payments API. ``capture=false`` authorises without taking money, which is
what the auth lane and the kill ladder need. Checkout.com reports the outcome
in ``status`` and the reason in ``response_code`` / ``response_summary``, so the
adapter derives a taxonomy code from both and keeps the summary in the message.
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

_APPROVED = frozenset({"Authorized", "Captured", "Card Verified"})
_PENDING = frozenset({"Pending"})


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"error": {"code": "malformed_response", "message": response.text[:200]}}
    return payload if isinstance(payload, dict) else {"error": {"code": "malformed_response"}}


# Checkout.com declines: response_summary is human text, response_code is
# numeric. Map the text first so a new numeric code still classifies.
_REFUSAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"insufficient|not enough (?:funds|balance)|not sufficient", re.I),
        "insufficient_funds",
    ),
    (re.compile(r"expired", re.I), "expired_card"),
    (
        re.compile(
            r"invalid (?:card )?number|card number.*invalid|invalid account", re.I
        ),
        "incorrect_number",
    ),
    (re.compile(r"\bcvv\b|\bcvc\b", re.I), "incorrect_cvc"),
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
    # No catch-all "declined" pattern here: a generic word must not shadow a
    # precise numeric response_code below. The fallback is generic_decline.
)

_RESPONSE_CODES: dict[str, str] = {
    "20051": "insufficient_funds",
    "20054": "expired_card",
    "20005": "generic_decline",
    "20100": "stolen_card",
    "20101": "lost_card",
    "20014": "incorrect_number",
    "20015": "incorrect_cvc",
}


def refusal_code(summary: str, response_code: str = "") -> str:
    """Derive a taxonomy code from Checkout.com's summary and numeric code.

    The human summary wins when it names a specific reason; otherwise the
    numeric ``response_code`` is authoritative. A blank summary must never
    short-circuit into ``generic_decline``.
    """
    text = summary.replace("_", " ").strip()
    if text:
        for pattern, code in _REFUSAL_PATTERNS:
            if pattern.search(text):
                return code
    return _RESPONSE_CODES.get(response_code, "generic_decline")


class CheckoutAdapter:
    name = "checkout"

    def __init__(self, settings: Settings, *, base_url: str | None = None) -> None:
        self.version = "2024-06-01"
        self.base_url = (base_url or settings.checkout_api_base).rstrip("/")
        self.currency = settings.checkout_currency.upper()

    # --- plumbing -----------------------------------------------------------
    def _headers(self, key: Key) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {key.secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _card_source(self, card: Card) -> dict[str, object]:
        return {
            "type": "card",
            "number": card.number,
            "expiry_month": card.exp_month,
            "expiry_year": card.exp_year,
            "cvv": card.cvv,
        }

    # --- adapter contract ---------------------------------------------------
    async def auth(
        self,
        client: httpx.AsyncClient,
        key: Key,
        card: Card,
        amount_cents: int,
        capture: bool,
    ) -> RawResult:
        amount = int(amount_cents) if amount_cents > 0 else 100
        payload: dict[str, object] = {
            "source": self._card_source(card),
            "amount": amount,
            "currency": self.currency,
            "capture": capture,
            "reference": f"sb-{uuid4().hex[:24]}",
            "processing": {"instruction": "NO_ACTION"},
        }
        if card.postal or card.country:
            payload["billing_address"] = {
                "zip": card.postal,
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
                f"{self.base_url}/payments/{ref}/voids", headers=self._headers(key), json={}
            )
        except httpx.HTTPError:
            return

    async def capture(
        self, client: httpx.AsyncClient, key: Key, ref: str, amount_cents: int
    ) -> RawResult:
        response = await client.post(
            f"{self.base_url}/payments/{ref}/captures",
            headers=self._headers(key),
            json={
                "amount": int(amount_cents) if amount_cents > 0 else 100,
                "reference": f"capture-{ref}",
            },
        )
        return self._map_payment(response)

    async def validate_key(self, client: httpx.AsyncClient, key: Key) -> bool:
        """A free ``GET /payments`` read. 401/403 means the key is not usable."""
        try:
            response = await client.get(
                f"{self.base_url}/payments", headers=self._headers(key), params={"limit": 1}
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    # --- mapping ------------------------------------------------------------
    def _map_payment(self, response: httpx.Response) -> RawResult:
        body = _json(response)
        ref = str(body.get("id") or "")
        if response.status_code >= 400:
            code = str(body.get("error_type") or body.get("error_codes") or "")
            if response.status_code == 422:
                # Card data rejected at validation time: a decline, not a fault.
                return RawResult(
                    402,
                    {
                        "error": {
                            "code": refusal_code(code),
                            "message": code or "invalid card",
                        }
                    },
                    provider_ref=ref,
                )
            return RawResult(
                response.status_code,
                {"error": {"code": code or f"http_{response.status_code}", "message": code}},
                provider_ref=ref,
            )

        if _as_mapping(body.get("error")):
            # A 2xx with an error envelope is a transport/proxy fault.
            return RawResult(502, body, provider_ref=ref)

        status = str(body.get("status") or "")
        source = _as_mapping(body.get("source"))
        avs = str(source.get("avs_check") or "")
        cvc = str(source.get("cvv_check") or "")

        if status in _APPROVED:
            return RawResult(200, body, provider_ref=ref, avs=avs, cvc=cvc, three_ds="N")

        if status in _PENDING:
            three_ds = _as_mapping(body.get("3ds"))
            redirect = str(
                body.get("redirect_url") or three_ds.get("redirect_url") or ""
            )
            if redirect:
                return RawResult(
                    402,
                    {"error": {"code": "authentication_required", "message": status}},
                    provider_ref=ref,
                    acs_url=redirect,
                    three_ds="C",
                    avs=avs,
                    cvc=cvc,
                )
            # Not terminal: no credit moves.
            return RawResult(202, body, provider_ref=ref, avs=avs, cvc=cvc)

        if status == "Declined":
            summary = str(body.get("response_summary") or "").strip()
            numeric = str(body.get("response_code") or "")
            return RawResult(
                402,
                {
                    "error": {
                        "code": refusal_code(summary, numeric),
                        "message": summary or "Declined",
                    }
                },
                provider_ref=ref,
                avs=avs,
                cvc=cvc,
            )

        # Anything else is not terminal: no credit moves.
        return RawResult(202, body, provider_ref=ref, avs=avs, cvc=cvc)
