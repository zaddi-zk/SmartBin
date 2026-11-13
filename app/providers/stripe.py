"""Stripe adapter (section 9) — SetupIntent for auth, manual capture for kill.

Pinned API version, livemode asserted on ``/v1/balance`` before a key can go
READY. Card declines arrive as HTTP 200 with ``last_setup_error`` /
``last_payment_error``, so the adapter re-shapes them into the error envelope
the taxonomy understands. That reshaping is provider knowledge, not verdict
logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx

from app.config import Settings
from app.core.parse import Card
from app.providers.base import Key, RawResult


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


class StripeAdapter:
    name = "stripe"

    def __init__(self, settings: Settings, *, base_url: str | None = None) -> None:
        self.version = settings.stripe_api_version
        self.currency = settings.stripe_currency
        self.base_url = (base_url or settings.stripe_api_base).rstrip("/")

    # --- plumbing -----------------------------------------------------------
    def _headers(self, key: Key, idem: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {key.secret}",
            "Stripe-Version": self.version,
            "Idempotency-Key": idem,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _card_form(self, card: Card) -> dict[str, str]:
        form = {
            "payment_method_data[type]": "card",
            "payment_method_data[card][number]": card.number,
            "payment_method_data[card][exp_month]": str(card.exp_month),
            "payment_method_data[card][exp_year]": str(card.exp_year),
            "payment_method_data[card][cvc]": card.cvv,
            "expand[]": "payment_method",
        }
        if card.postal:
            form["payment_method_data[billing_details][address][postal_code]"] = card.postal
        if card.country:
            form["payment_method_data[billing_details][address][country]"] = card.country
        if card.holder:
            form["payment_method_data[billing_details][name]"] = card.holder
        return form

    # --- adapter contract ---------------------------------------------------
    async def auth(
        self,
        client: httpx.AsyncClient,
        key: Key,
        card: Card,
        amount_cents: int,
        capture: bool,
    ) -> RawResult:
        if capture:
            payload = self._card_form(card)
            payload.update(
                {
                    "amount": str(max(50, amount_cents)),
                    "currency": self.currency,
                    "capture_method": "manual",
                    "confirm": "true",
                    "usage": "off_session",
                }
            )
            response = await client.post(
                f"{self.base_url}/v1/payment_intents",
                headers=self._headers(key, f"auth-{uuid4()}"),
                data=payload,
            )
            return self._map_intent(response)

        payload = self._card_form(card)
        payload.update({"confirm": "true", "usage": "off_session"})
        response = await client.post(
            f"{self.base_url}/v1/setup_intents",
            headers=self._headers(key, f"auth-{uuid4()}"),
            data=payload,
        )
        return self._map_intent(response)

    async def cancel(self, client: httpx.AsyncClient, key: Key, ref: str) -> None:
        if not ref:
            return
        if ref.startswith("seti_"):
            url = f"{self.base_url}/v1/setup_intents/{ref}/cancel"
        else:
            url = f"{self.base_url}/v1/payment_intents/{ref}/cancel"
        try:
            await client.post(
                url,
                headers=self._headers(key, f"cancel-{ref}"),
                data={},
            )
        except httpx.HTTPError:
            # Cancelling an already-cancelled hold is not an error worth raising.
            return

    async def capture(
        self, client: httpx.AsyncClient, key: Key, ref: str, amount_cents: int
    ) -> RawResult:
        response = await client.post(
            f"{self.base_url}/v1/payment_intents/{ref}/capture",
            headers=self._headers(key, f"capture-{ref}"),
            data={"amount_to_capture": str(max(50, amount_cents))},
        )
        return self._map_intent(response)

    async def validate_key(self, client: httpx.AsyncClient, key: Key) -> bool:
        try:
            response = await client.get(
                f"{self.base_url}/v1/balance",
                headers=self._headers(key, f"validate-{uuid4()}"),
            )
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            return False
        body = _as_mapping(_json(response))
        return body.get("livemode") is True

    # --- mapping ------------------------------------------------------------
    def _map_intent(self, response: httpx.Response) -> RawResult:
        body = _json(response)
        ref = str(body.get("id") or "")
        if response.status_code >= 400:
            return RawResult(response.status_code, body, provider_ref=ref)

        # Stripe never returns a top-level error with 2xx. If we see one the
        # body is malformed (or a proxy injected a page), which is a transport
        # fault, not a card verdict. RETRY keeps the user's credit intact.
        if _as_mapping(body.get("error")):
            return RawResult(502, body, provider_ref=ref)

        error = _as_mapping(body.get("last_setup_error")) or _as_mapping(
            body.get("last_payment_error")
        )
        if error:
            return RawResult(402, {"error": dict(error)}, provider_ref=ref)

        status = str(body.get("status") or "")
        if status == "requires_action":
            next_action = _as_mapping(body.get("next_action"))
            redirect = _as_mapping(next_action.get("redirect_to_url"))
            return RawResult(
                402,
                {"error": {"code": "authentication_required", "message": "3DS challenge"}},
                provider_ref=ref,
                acs_url=str(redirect.get("url") or ""),
                three_ds="C",
            )

        if status in ("succeeded", "requires_capture", "processing"):
            checks = _as_mapping(_as_mapping(body.get("payment_method")).get("checks"))
            return RawResult(
                200,
                body,
                provider_ref=ref,
                avs=str(
                    checks.get("address_postal_code_check")
                    or checks.get("address_line1_postal_code_check")
                    or ""
                ),
                cvc=str(checks.get("cvc_check") or ""),
                three_ds="N",
            )

        return RawResult(
            402,
            {"error": {"code": "generic_decline", "message": status or "unknown"}},
            provider_ref=ref,
        )


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"error": {"code": "malformed_response", "message": response.text[:200]}}
    return payload if isinstance(payload, dict) else {"error": {"code": "malformed_response"}}
