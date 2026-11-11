from __future__ import annotations

import asyncio
import hashlib
import hmac
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

# Stripe public test cards
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
    """Multi-gate payment processor. Routes to correct API based on gate_name."""

    def __init__(self, config: Config):
        self.config = config
        self.global_sem = asyncio.Semaphore(60)
        self.gate_sems: dict[str, asyncio.Semaphore] = {}

    def _sem(self, name: str) -> asyncio.Semaphore:
        if name not in self.gate_sems:
            self.gate_sems[name] = asyncio.Semaphore(12)
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
                if self.config.stripe_test_mode:
                    await asyncio.sleep(random.uniform(0.001, 0.005))
                else:
                    await asyncio.sleep(random.uniform(0.05, 0.15))
                
                started = time.perf_counter()
                try:
                    if self.config.stripe_test_mode:
                        raw = dict(self._simulate(card))
                    else:
                        # Route to correct gate based on gate_name
                        raw = await self._route_to_gate(gate_name, card, amount_minor, proxy)
                except httpx.TimeoutException:
                    raw = {"timeout": True, "error": "timeout"}
                except Exception as exc:
                    raw = {"error": str(exc)}
                
                raw["latency_ms"] = int((time.perf_counter() - started) * 1000)
                raw["gate"] = gate_name
                raw["amount_band"] = amount_minor
                raw["proxy_id"] = proxy_id
                raw["purpose"] = purpose
                raw["canary_ok"] = canary_ok
                return raw

    async def _route_to_gate(
        self, gate_name: str, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        """Route to the correct payment gateway based on gate_name."""
        gate_handlers = {
            "stripe": lambda: self._stripe(card, amount_minor, proxy),
            "authnet": lambda: self._authnet(card, amount_minor, proxy),
            "adyen": lambda: self._adyen(card, amount_minor, proxy),
            "cybersource": lambda: self._cybersource(card, amount_minor, proxy),
            "checkout": lambda: self._checkout(card, amount_minor, proxy),
            "worldpay": lambda: self._worldpay(card, amount_minor, proxy),
            "shopify": lambda: self._shopify(card, amount_minor, proxy),
            "paypal": lambda: self._paypal(card, amount_minor, proxy),
            "braintree": lambda: self._braintree(card, amount_minor, proxy),
            "square": lambda: self._square(card, amount_minor, proxy),
        }
        
        handler = gate_handlers.get(gate_name)
        if handler:
            return await handler()
        
        # Default to Stripe if gate not found
        log.warning("event=unknown_gate gate=%s defaulting_to_stripe", gate_name)
        return await self._stripe(card, amount_minor, proxy)

    def _simulate(self, card: Card) -> dict[str, Any]:
        result = TEST_CARDS.get(card.number)
        if result is None:
            return {"decline_code": "generic_decline"}
        return dict(result)

    # ========== STRIPE ==========
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

    # ========== AUTHNET ==========
    async def _authnet(
        self, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        """Authorize.net AIM API."""
        api_login_id = self.config.authnet_api_login_id
        transaction_key = self.config.authnet_transaction_key
        
        if not api_login_id or not transaction_key:
            return {"error": "AUTHNET credentials missing", "decline_code": "generic_decline"}
        
        # Generate MD5 hash
        md5_hash = hashlib.md5(
            f"{api_login_id}|{transaction_key}|{amount_minor}|1".encode()
        ).hexdigest()
        
        data = {
            "x_login": api_login_id,
            "x_trans_type": "auth",
            "x_amount": str(amount_minor / 100),
            "x_card_num": card.number,
            "x_exp_date": f"{card.exp_month}{card.exp_year}",
            "x_card_code": card.cvv,
            "x_md5_hash": md5_hash,
            "x_delim_data": "TRUE",
            "x_delim_data": "|",
        }
        
        async with make_client(proxy=proxy, timeout=12.0) as client:
            response = await client.post(
                "https://secure.authorize.net/gateway/transact.dll",
                data=data,
            )
            try:
                payload = response.json()
            except ValueError:
                # Parse delimited response
                parts = response.text.split("|")
                if len(parts) >= 2 and parts[0] == "1":
                    return {"approved": True, "status": "authorized"}
                return {
                    "decline_code": parts[2] if len(parts) > 2 else "generic_decline",
                    "raw_code": parts[1] if len(parts) > 1 else "",
                    "error": parts[3] if len(parts) > 3 else "",
                }
            
            if payload.get("responseCode") == "1":
                return {"approved": True, "status": "authorized"}
            return {
                "decline_code": payload.get("messages", {}).get("message", [{}])[0].get("code", ""),
                "raw_code": payload.get("responseCode", ""),
                "error": payload.get("messages", {}).get("message", [{}])[0].get("text", ""),
            }

    # ========== ADYEN ==========
    async def _adyen(
        self, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        """Adyen Checkout API."""
        api_key = self.config.adyen_api_key
        merchant_account = self.config.adyen_merchant_account
        
        if not api_key or not merchant_account:
            return {"error": "ADYEN credentials missing", "decline_code": "generic_decline"}
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "adyen-idempotency-key": hashlib.sha256(time.strftime("%Y%m%d%H%M%S").encode()).hexdigest(),
        }
        
        payload = {
            "amount": {"value": amount_minor, "currency": "USD"},
            "reference": f"SMARTBIN-{int(time.time())}",
            "merchantAccount": merchant_account,
            "paymentMethod": {
                "type": "scheme",
                "number": card.number,
                "expiryMonth": str(card.exp_month).zfill(2),
                "expiryYear": str(card.exp_year),
                "cvc": card.cvv,
            },
            "options": {"totalInterimTransactionsEnabled": True},
        }
        
        async with make_client(proxy=proxy, timeout=12.0) as client:
            response = await client.post(
                "https://checkout-test.adyen.com/v68/api/payment",
                headers=headers,
                json=payload,
            )
            result = response.json()
            
            if result.get("resultCode") in ("Authorised", "AuthorisedWithAdditionalInfo"):
                return {"approved": True, "status": "authorised"}
            
            return {
                "decline_code": result.get("resultCode", "Refused"),
                "error": result.get("refusalReason", ""),
            }

    # ========== CYBERSOURCE ==========
    async def _cybersource(
        self, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        """Cybersource Secure Acceptance API."""
        merchant_id = self.config.cybersource_merchant_id
        api_key = self.config.cybersource_api_key
        api_secret = self.config.cybersource_api_secret
        
        if not merchant_id or not api_key or not api_secret:
            return {"error": "CYBERSOURCE credentials missing", "decline_code": "generic_decline"}
        
        timestamp = str(int(time.time()))
        nonce = hashlib.sha256(random.randbytes(16)).hexdigest()[:16]
        
        signature_base = f"{merchant_id}|{timestamp}|{nonce}|{amount_minor}|USD"
        signature = hmac.new(
            api_secret.encode(), signature_base.encode(), hashlib.sha256
        ).hexdigest()
        
        headers = {
            "CyberSourceSecuritySignature": signature,
            "CyberSourceSecurityTimestamp": timestamp,
            "CyberSourceSecurityNonce": nonce,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "id": f"SMARTBIN-{int(time.time())}",
            "merchantId": merchant_id,
            "request": {
                "amount": amount_minor,
                "currency": "USD",
                "payment": {
                    "card": {
                        "account": card.number,
                        "expirationMonth": str(card.exp_month).zfill(2),
                        "expirationYear": str(card.exp_year),
                        "securityCode": card.cvv,
                    }
                },
            },
        }
        
        async with make_client(proxy=proxy, timeout=12.0) as client:
            response = await client.post(
                "https://api.cybersource.com/commerce/v2/transactions/sales",
                headers=headers,
                json=payload,
            )
            result = response.json()
            
            status = result.get("status", "")
            if status == "SUCCESS":
                return {"approved": True, "status": "success"}
            
            return {
                "decline_code": result.get("reason", "DECLINED"),
                "error": result.get("message", ""),
            }

    # ========== CHECKOUT.COM ==========
    async def _checkout(
        self, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        """Checkout.com Payments API."""
        api_key = self.config.checkout_api_key
        
        if not api_key:
            return {"error": "CHECKOUT credentials missing", "decline_code": "generic_decline"}
        
        headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
            "Idempotency-Key": hashlib.sha256(time.strftime("%Y%m%d%H%M%S").encode()).hexdigest(),
        }
        
        payload = {
            "amount": amount_minor,
            "currency": "USD",
            "source": {
                "type": "card",
                "number": card.number,
                "expiry_month": str(card.exp_month).zfill(2),
                "expiry_year": str(card.exp_year),
                "cvv": card.cvv,
            },
            "capture": False,
        }
        
        async with make_client(proxy=proxy, timeout=12.0) as client:
            response = await client.post(
                "https://api.sandbox.checkout.com/tokenized-charges",
                headers=headers,
                json=payload,
            )
            result = response.json()
            
            if result.get("status") == "CAPTURED" or result.get("response") == "APPROVED":
                return {"approved": True, "status": "captured"}
            
            return {
                "decline_code": result.get("failure_reason", "DECLINED"),
                "error": result.get("message", ""),
            }

    # ========== WORLDPAY ==========
    async def _worldpay(
        self, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        """Worldpay REST API."""
        merchant_code = self.config.worldpay_merchant_code
        api_key = self.config.worldpay_api_key
        
        if not merchant_code or not api_key:
            return {"error": "WORLDPAY credentials missing", "decline_code": "generic_decline"}
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        payload = {
            "amount": amount_minor,
            "currency": "USD",
            "type": "AUTHORISATION",
            "capture": False,
            "card": {
                "number": card.number,
                "expiryMonth": str(card.exp_month).zfill(2),
                "expiryYear": str(card.exp_year),
                "cvc": card.cvv,
            },
        }
        
        async with make_client(proxy=proxy, timeout=12.0) as client:
            response = await client.post(
                f"https://sandboxsecure.wp3d.net/rest/v1/{merchant_code}/payments",
                headers=headers,
                json=payload,
            )
            result = response.json()
            
            if result.get("status") == "AUTHORISED":
                return {"approved": True, "status": "authorised"}
            
            return {
                "decline_code": result.get("responseCode", "DECLINED"),
                "error": result.get("responseText", ""),
            }

    # ========== SHOPIFY ==========
    async def _shopify(
        self, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        """Shopify Payment API (via Charge)."""
        api_key = self.config.shopify_api_key
        
        if not api_key:
            return {"error": "SHOPIFY credentials missing", "decline_code": "generic_decline"}
        
        headers = {
            "X-Shopify-Access-Token": api_key,
            "Content-Type": "application/json",
        }
        
        payload = {
            "charge": {
                "amount": str(amount_minor / 100),
                "currency": "USD",
                "source": {
                    "type": "credit_card",
                    "number": card.number,
                    "month": card.exp_month,
                    "year": card.exp_year,
                    "verification_value": card.cvv,
                },
            },
        }
        
        async with make_client(proxy=proxy, timeout=12.0) as client:
            response = await client.post(
                "https://api.shopify.com/channels/storefront/graphql",
                headers=headers,
                json=payload,
            )
            result = response.json()
            
            if result.get("data", {}).get("charge", {}).get("status") == "success":
                return {"approved": True, "status": "success"}
            
            return {
                "decline_code": result.get("errors", ["UNKNOWN"])[0],
                "error": str(result.get("errors", [])),
            }

    # ========== PAYPAL ==========
    async def _paypal(
        self, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        """PayPal Payments API."""
        client_id = self.config.paypal_client_id
        client_secret = self.config.paypal_client_secret
        
        if not client_id or not client_secret:
            return {"error": "PAYPAL credentials missing", "decline_code": "generic_decline"}
        
        # Get access token
        token_data = {
            "grant_type": "client_credentials",
        }
        
        async with make_client(proxy=proxy, timeout=12.0) as client:
            token_response = await client.post(
                "https://api-m.sandbox.paypal.com/v1/oauth2/token",
                auth=(client_id, client_secret),
                data=token_data,
            )
            token_result = token_response.json()
            access_token = token_result.get("access_token")
            
            if not access_token:
                return {"error": "PAYPAL token fetch failed", "decline_code": "generic_decline"}
            
            # Create payment
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            
            payload = {
                "intent": "sale",
                "payer": {
                    "payment_method": "credit_card",
                    "funding_instruments": [
                        {
                            "credit_card": {
                                "number": card.number,
                                "type": "visa",
                                "expire_month": str(card.exp_month).zfill(2),
                                "expire_year": str(card.exp_year),
                                "cvv2": card.cvv,
                            }
                        }
                    ],
                },
                "transactions": [
                    {
                        "amount": {
                            "total": str(amount_minor / 100),
                            "currency": "USD",
                        },
                    }
                ],
            }
            
            payment_response = await client.post(
                "https://api-m.sandbox.paypal.com/v1/payments/payment",
                headers=headers,
                json=payload,
            )
            payment_result = payment_response.json()
            
            if payment_result.get("state") == "approved":
                return {"approved": True, "status": "approved"}
            
            return {
                "decline_code": payment_result.get("state", "failed"),
                "error": str(payment_result.get("payer", {}).get("payment_method", "")),
            }

    # ========== BRAINTREE ==========
    async def _braintree(
        self, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        """Braintree REST API."""
        merchant_id = self.config.braintree_merchant_id
        public_key = self.config.braintree_public_key
        private_key = self.config.braintree_private_key
        
        if not merchant_id or not public_key or not private_key:
            return {"error": "BRAINTREE credentials missing", "decline_code": "generic_decline"}
        
        auth = (public_key, private_key)
        headers = {"Content-Type": "application/xml"}
        
        payload = f"""<?xml version="1.0" encoding="utf-8"?>
<transaction>
    <type>sale</type>
    <amount>{amount_minor / 100}</amount>
    <credit_card>
        <number>{card.number}</number>
        <month>{card.exp_month:02d}</month>
        <year>{card.exp_year}</year>
        <verification_value>{card.cvv}</verification_value>
    </credit_card>
    <options>
        <submit_for_settlement>false</submit_for_settlement>
    </options>
</transaction>"""
        
        async with make_client(proxy=proxy, timeout=12.0) as client:
            response = await client.post(
                f"https://api.braintreegateway.com/merchants/{merchant_id}/transactions.xml",
                auth=auth,
                headers=headers,
                data=payload,
            )
            
            # Parse XML response
            if "approved" in response.text.lower():
                return {"approved": True, "status": "approved"}
            
            if "declined" in response.text.lower():
                return {"decline_code": "DECLINED", "error": "Card declined"}
            
            return {"error": response.text[:200], "decline_code": "UNKNOWN"}

    # ========== SQUARE ==========
    async def _square(
        self, card: Card, amount_minor: int, proxy: str | None
    ) -> dict[str, Any]:
        """Square Cards API."""
        access_token = self.config.square_access_token
        
        if not access_token:
            return {"error": "SQUARE credentials missing", "decline_code": "generic_decline"}
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "idempotency_key": hashlib.sha256(time.strftime("%Y%m%d%H%M%S").encode()).hexdigest(),
            "card": {
                "card_number": card.number,
                "exp_month": int(card.exp_month),
                "exp_year": int(card.exp_year),
                "cvv": card.cvv,
            },
            "amount_money": {
                "amount": amount_minor,
                "currency": "USD",
            },
            "idempotency_key": f"SMARTBIN-{int(time.time())}",
        }
        
        async with make_client(proxy=proxy, timeout=12.0) as client:
            response = await client.post(
                "https://connect.squareupsandbox.com/v2/payments",
                headers=headers,
                json=payload,
            )
            result = response.json()
            
            status = result.get("payment", {}).get("status", "")
            if status == "COMPLETED":
                return {"approved": True, "status": "completed"}
            
            return {
                "decline_code": result.get("errors", [{}])[0].get("code", "DECLINED"),
                "error": result.get("errors", [{}])[0].get("message", ""),
            }

    async def test_canary(self, kind: str, card_raw: str, proxy: str | None = None) -> dict[str, Any]:
        card = Card.parse(card_raw)
        return await self.dispatch(
            card,
            amount_minor=50,
            proxy=proxy,
            purpose=f"canary-{kind}",
            canary_ok=1,
        )
