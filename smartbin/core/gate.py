from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from config import Config
from models import Card
from util_net import make_client

log = logging.getLogger("smartbin.gate")

# Stripe public test cards. In test mode these are the only deterministic outcomes.
TEST_CARDS: dict[str, dict[str, Any]] = {
    "4242424242424242": {"approved": True},
    "4000000000000002": {"decline_code": "generic_decline"},
    "4000000000009995": {"decline_code": "insufficient_funds"},
    "4000000000009987": {"decline_code": "pickup_card"},
    "4000000000000069": {"decline_code": "expired_card"},
    "4000000000000127": {"decline_code": "incorrect_cvc"},
    "4000000000003220": {
        "decline_code": "authentication_required",
        "acs_host": "acs.stripe.com",
        "status": "requires_action",
    },
}


class GateManager:
    """Stripe auth-and-void. PEAK MODE: higher concurrency for speed."""

    def __init__(self, config: Config):
        self.config = config
        self.global_sem = asyncio.Semaphore(60)  # was 40 - more parallel checks
        self.gate_sems: dict[str, asyncio.Semaphore] = {}

    def _sem(self, name: str) -> asyncio.Semaphore:
        if name not in self.gate_sems:
            self.gate_sems[name] = asyncio.Semaphore(12)  # was 8 - faster per-gate
        return self.gate_sems[name]

    async def dispatch(
        self,
        card: Card,
        amount_minor: int,
        proxy: str | None = None,
        purpose: str = "check",
        gate_name: str = "stripe",
        canary_ok: int = 1,
        proxy_id: int = 0,
    ) -> dict[str, Any]:
        async with self.global_sem:
            async with self._sem(gate_name):
                # PEAK MODE: minimal delay for speed
                if self.config.stripe_test_mode:
                    await asyncio.sleep(random.uniform(0.001, 0.005))
                else:
                    await asyncio.sleep(random.uniform(0.05, 0.15))  # was 0.2s base
                started = time.perf_counter()
                try:
                    if self.config.stripe_test_mode:
                        raw = dict(self._simulate(card))
                    else:
                        raw = await self._stripe(card, amount_minor, proxy)
                except httpx.TimeoutException:
                    raw = {"timeout": True, "error": "timeout"}
                except Exception as exc:  # network / parse failures are verdicts too
                    raw = {"error": str(exc)}
                raw["latency_ms"] = int((time.perf_counter() - started) * 1000)
                raw["gate"] = gate_name
                raw["amount_band"] = amount_minor
                raw["proxy_id"] = proxy_id
                raw["purpose"] = purpose
                raw["canary_ok"] = canary_ok
                return raw

    def _simulate(self, card: Card) -> dict[str, Any]:
        result = TEST_CARDS.get(card.number)
        if result is None:
            # Unknown cards never get a fake approval in test mode.
            return {"decline_code": "generic_decline"}
        return dict(result)

    async def _stripe(
        self, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        if not self.config.stripe_api_key:
            log.error("event=stripe_no_api_key card=%s", card.masked())
            return {"error": "STRIPE_API_KEY missing", "decline_code": "generic_decline"}
        auth = (self.config.stripe_api_key, "")
        async with make_client(proxy=proxy, timeout=12.0) as client:
            pm = await self._stripe_post(
                client,
                "https://api.stripe.com/v1/payment_methods",
                auth,
                {
                    "type": "card",
                    "card[number]": card.number,
                    "card[exp_month]": str(card.exp_month),
                    "card[exp_year]": str(card.exp_year),
                    "card[cvc]": card.cvv,
                },
            )
            if "error" in pm:
                return pm
            pm_id = pm.get("id")
            if not pm_id:
                return {"error": "payment_method missing id"}

            intent = await self._stripe_post(
                client,
                "https://api.stripe.com/v1/payment_intents",
                auth,
                {
                    "amount": str(amount_minor),
                    "currency": "eur" if amount_minor >= 100 else "usd",
                    "payment_method": str(pm_id),
                    "payment_method_types[]": "card",
                    "confirmation_method": "manual",
                    "capture_method": "manual",
                    "confirm": "true",
                    "description": "SMARTBIN auth-and-void",
                },
            )
            if "error" in intent:
                return intent

            status = str(intent.get("status") or "")
            if status in ("succeeded", "requires_capture"):
                await self._stripe_post(
                    client,
                    f"https://api.stripe.com/v1/payment_intents/{intent.get('id')}/cancel",
                    auth,
                    {},
                    tolerate_errors=True,
                )
                return {"approved": True, "status": status}

            if status == "requires_action":
                url = (
                    ((intent.get("next_action") or {}).get("redirect_to_url") or {}).get("url")
                    or ""
                )
                host = urlsplit(url).hostname or ""
                return {
                    "decline_code": "authentication_required",
                    "acs_host": host,
                    "status": status,
                }

            last = intent.get("last_payment_error") or {}
            return {
                "decline_code": str(last.get("decline_code") or last.get("code") or ""),
                "raw_code": str(last.get("code") or ""),
                "error": str(last.get("message") or ""),
                "status": status,
            }

    async def _stripe_post(
        self,
        client: httpx.AsyncClient,
        url: str,
        auth: tuple[str, str],
        data: dict[str, str],
        tolerate_errors: bool = False,
    ) -> dict[str, Any]:
        delay = 1.0
        for attempt in range(3):
            response = await client.post(url, auth=auth, data=data)
            if response.status_code == 429 and attempt < 2:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if response.status_code >= 400:
                if tolerate_errors:
                    return payload if isinstance(payload, dict) else {}
                error = payload.get("error") if isinstance(payload, dict) else {}
                return {
                    "error": str((error or {}).get("message") or response.text[:200]),
                    "decline_code": str((error or {}).get("decline_code") or ""),
                    "raw_code": str((error or {}).get("code") or ""),
                }
            return payload if isinstance(payload, dict) else {}
        return {"error": "stripe rate limited"}

    async def test_canary(self, kind: str, card_raw: str, proxy: str | None = None) -> dict[str, Any]:
        card = Card.parse(card_raw)
        return await self.dispatch(
            card,
            amount_minor=50,
            proxy=proxy,
            purpose=f"canary-{kind}",
            canary_ok=1,
        )
