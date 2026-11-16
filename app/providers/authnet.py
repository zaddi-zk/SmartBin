"""Authorize.Net adapter (section 9).

Auth is an ``authOnlyTransaction``; charge is ``authCaptureTransaction``; kill
uses ``authOnlyTransaction`` plus ``priorAuthCaptureTransaction`` and
``voidTransaction``. Authorize.Net returns a BOM-prefixed JSON body, reports
the outcome in ``transactionResponse.responseCode`` and the reason in
``errors[].errorText``, so the adapter derives a taxonomy code from that text
and keeps the raw text in the message. Verdicts stay in
``app.core.taxonomy``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx

from app.config import Settings
from app.core.parse import Card
from app.providers.base import Key, RawResult

_KEY_DEAD_CODES = frozenset({"E00007", "E00009", "E00010"})

# Authorize.Net has no decline_code. Map the human text onto the shared
# taxonomy. Order matters: "insufficient funds" must win over "declined".
_REFUSAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"insufficient|not enough (?:funds|balance)|not sufficient", re.I),
        "insufficient_funds",
    ),
    (re.compile(r"expired", re.I), "expired_card"),
    (
        re.compile(
            r"invalid (?:credit )?card number|card number is invalid|"
            r"invalid account|no account",
            re.I,
        ),
        "incorrect_number",
    ),
    (re.compile(r"\bcvv\b|\bcvc\b|card code", re.I), "incorrect_cvc"),
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
    (re.compile(r"duplicate", re.I), "duplicate_transaction"),
    (re.compile(r"declined|refused|denied", re.I), "generic_decline"),
)


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json(response: httpx.Response) -> dict[str, Any]:
    text = response.text.lstrip("\ufeff").strip()
    if not text:
        return {"error": {"code": "malformed_response", "message": "empty body"}}
    try:
        payload = json.loads(text)
    except ValueError:
        return {"error": {"code": "malformed_response", "message": text[:200]}}
    return payload if isinstance(payload, dict) else {"error": {"code": "malformed_response"}}


def refusal_code(reason: str) -> str:
    """Derive a taxonomy code from Authorize.Net's free-text decline reason."""
    for pattern, code in _REFUSAL_PATTERNS:
        if pattern.search(reason):
            return code
    return "generic_decline"


def _error_code(body: Mapping[str, Any]) -> str:
    messages = _as_mapping(body.get("messages"))
    entries = messages.get("message")
    if isinstance(entries, list) and entries:
        first = _as_mapping(entries[0])
        return str(first.get("code") or "")
    return ""


def _error_text(body: Mapping[str, Any]) -> str:
    txn = _as_mapping(body.get("transactionResponse"))
    errors = txn.get("errors")
    if isinstance(errors, list) and errors:
        first = _as_mapping(errors[0])
        return str(first.get("errorText") or "")
    messages = _as_mapping(body.get("messages"))
    entries = messages.get("message")
    if isinstance(entries, list) and entries:
        first = _as_mapping(entries[0])
        return str(first.get("text") or "")
    return ""


class AuthNetAdapter:
    name = "authnet"

    def __init__(self, settings: Settings, *, base_url: str | None = None) -> None:
        self.version = "2024-01"
        self.base_url = base_url or settings.authnet_api_base
        self.login = settings.authnet_login_id.get_secret_value()
        self.transaction_key = settings.authnet_transaction_key.get_secret_value()

    # --- plumbing -----------------------------------------------------------
    def _credentials(self, key: Key) -> tuple[str, str]:
        """A key row stores ``login|transaction_key``; env is the fallback."""
        if "|" in key.secret:
            login, _, transaction_key = key.secret.partition("|")
            return login, transaction_key
        return self.login, self.transaction_key

    def _merchant(self, key: Key) -> dict[str, str]:
        login, transaction_key = self._credentials(key)
        return {"name": login, "transactionKey": transaction_key}

    def _card_payload(self, card: Card) -> dict[str, object]:
        payload: dict[str, object] = {
            "cardNumber": card.number,
            "expirationDate": f"{card.exp_year:04d}-{card.exp_month:02d}",
            "cardCode": card.cvv,
        }
        return payload

    async def _post(
        self, client: httpx.AsyncClient, key: Key, request: dict[str, object]
    ) -> RawResult:
        response = await client.post(
            self.base_url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={
                "createTransactionRequest": {
                    "merchantAuthentication": self._merchant(key),
                    **request,
                }
            },
        )
        return self._map(response)

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
        transaction: dict[str, object] = {
            "transactionType": "authCaptureTransaction" if capture else "authOnlyTransaction",
            "amount": f"{amount:.2f}",
            "payment": {"creditCard": self._card_payload(card)},
            "refId": f"sb-{uuid4().hex[:20]}",
        }
        if card.postal or card.country:
            transaction["billTo"] = {
                "zip": card.postal,
                "country": card.country or "US",
            }
        return await self._post(
            client,
            key,
            {
                "transactionRequest": transaction,
            },
        )

    async def cancel(self, client: httpx.AsyncClient, key: Key, ref: str) -> None:
        if not ref:
            return
        try:
            await client.post(
                self.base_url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json={
                    "createTransactionRequest": {
                        "merchantAuthentication": self._merchant(key),
                        "transactionRequest": {
                            "transactionType": "voidTransaction",
                            "refTransId": ref,
                        },
                    }
                },
            )
        except httpx.HTTPError:
            # Voiding an already-released authorisation is not an error.
            return

    async def capture(
        self, client: httpx.AsyncClient, key: Key, ref: str, amount_cents: int
    ) -> RawResult:
        amount = (int(amount_cents) if amount_cents > 0 else 100) / 100
        return await self._post(
            client,
            key,
            {
                "transactionRequest": {
                    "transactionType": "priorAuthCaptureTransaction",
                    "amount": f"{amount:.2f}",
                    "refTransId": ref,
                }
            },
        )

    async def validate_key(self, client: httpx.AsyncClient, key: Key) -> bool:
        """``authenticateTestRequest`` is free and proves the credentials."""
        try:
            response = await client.post(
                self.base_url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json={"authenticateTestRequest": {"merchantAuthentication": self._merchant(key)}},
            )
        except httpx.HTTPError:
            return False
        if response.status_code in (401, 403):
            return False
        body = _json(response)
        messages = _as_mapping(body.get("messages"))
        return response.status_code == 200 and messages.get("resultCode") == "Ok"

    # --- mapping ------------------------------------------------------------
    def _map(self, response: httpx.Response) -> RawResult:
        body = _json(response)
        txn = _as_mapping(body.get("transactionResponse"))
        ref = str(txn.get("transId") or "")
        messages = _as_mapping(body.get("messages"))
        result = str(messages.get("resultCode") or "")

        if response.status_code >= 400:
            return RawResult(response.status_code, body, provider_ref=ref)

        if result != "Ok" or not txn:
            code = _error_code(body)
            if code in _KEY_DEAD_CODES or response.status_code in (401, 403):
                return RawResult(
                    401,
                    {"error": {"code": "invalid_api_key", "message": _error_text(body)}},
                    provider_ref=ref,
                )
            return RawResult(
                502,
                {"error": {"code": "acquirer_error", "message": _error_text(body) or result}},
                provider_ref=ref,
            )

        response_code = str(txn.get("responseCode") or "")
        avs = str(txn.get("avsResultCode") or "")
        cvc = str(txn.get("cvvResultCode") or "")
        if response_code == "1":
            return RawResult(
                200,
                body,
                provider_ref=ref,
                avs=avs,
                cvc=cvc,
                three_ds="N",
            )
        if response_code == "4":
            # Held for review: the authorisation exists, no capture.
            return RawResult(
                402,
                {"error": {"code": "approve_with_id", "message": _error_text(body) or "held"}},
                provider_ref=ref,
                avs=avs,
                cvc=cvc,
            )

        reason = _error_text(body) or "declined"
        return RawResult(
            402,
            {"error": {"code": refusal_code(reason), "message": reason}},
            provider_ref=ref,
            avs=avs,
            cvc=cvc,
        )
