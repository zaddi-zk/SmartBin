"""Idempotent gate seeding.

Only gates whose adapter is actually installed are enabled. A seeded-but-off
gate still renders in the lane list (dim, tappable) so the user never sees a
blank screen, but the router will not send a card to it.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Gate, GateStateRow

ALL_COUNTRIES = ["*"]

GATE_SEED: tuple[dict[str, object], ...] = (
    {
        "id": "auth_stripe",
        "label": "Stripe Auth",
        "lane": "auth",
        "processor": "stripe",
        "primitive": "setup_intent",
        "amount_cents": 0,
        "capture": "none",
        "cost_cr": Decimal("1"),
        "countries": ALL_COUNTRIES,
        "handles_3ds": True,
        "key_country": "US",
        "blurb": "Widest issuer coverage. Default for general work.",
        "enabled": True,
        "sort_hint": 10,
    },
    {
        "id": "charge_stripe",
        "label": "Stripe Charge",
        "lane": "charge",
        "processor": "stripe",
        "primitive": "payment_intent",
        "amount_cents": 100,
        "capture": "manual_then_capture",
        "cost_cr": Decimal("2"),
        "countries": ALL_COUNTRIES,
        "handles_3ds": True,
        "key_country": "US",
        "blurb": "Real capture. Money moves on approve.",
        "enabled": True,
        "sort_hint": 10,
    },
    {
        "id": "kill_stripe",
        "label": "Stripe Kill",
        "lane": "kill",
        "processor": "stripe",
        "primitive": "payment_intent_manual",
        "amount_cents": 0,
        "capture": "manual",
        "cost_cr": Decimal("6"),
        "ladder": [50, 100, 500, 2000, 5000],
        "countries": ALL_COUNTRIES,
        "handles_3ds": True,
        "key_country": "US",
        "blurb": "Escalating ladder. Stops on approve and cancels the hold.",
        "enabled": True,
        "sort_hint": 10,
    },
    # --- seeded, disabled until their adapter lands -------------------------
    *(
        {
            "id": f"auth_{processor}",
            "label": f"{label} Auth",
            "lane": "auth",
            "processor": processor,
            "primitive": "auth",
            "amount_cents": 0,
            "capture": "none",
            "cost_cr": Decimal("1"),
            "countries": ALL_COUNTRIES,
            "handles_3ds": True,
            "key_country": "US",
            "blurb": "Adapter pending. Gate is visible but disabled.",
            "enabled": False,
            "sort_hint": 50,
        }
        for processor, label in (
            ("adyen", "Adyen"),
            ("authnet", "AuthNet"),
            ("braintree", "Braintree"),
            ("mollie", "Mollie"),
            ("cybersource", "Cybersource"),
            ("square", "Square"),
            ("vbv", "VBV"),
        )
    ),
    *(
        {
            "id": f"charge_{processor}",
            "label": f"{label} Charge",
            "lane": "charge",
            "processor": processor,
            "primitive": "payment_intent",
            "amount_cents": 100,
            "capture": "auto",
            "cost_cr": Decimal("2"),
            "countries": ALL_COUNTRIES,
            "handles_3ds": True,
            "key_country": "US",
            "blurb": "Adapter pending. Gate is visible but disabled.",
            "enabled": False,
            "sort_hint": 50,
        }
        for processor, label in (
            ("adyen", "Adyen"),
            ("authnet", "AuthNet"),
            ("checkout", "Checkout"),
            ("square", "Square"),
            ("cybersource", "Cybersource"),
        )
    ),
    *(
        {
            "id": f"kill_{processor}",
            "label": f"{label} Kill",
            "lane": "kill",
            "processor": processor,
            "primitive": "payment_intent_manual",
            "amount_cents": 0,
            "capture": "manual",
            "cost_cr": Decimal("6"),
            "ladder": [50, 100, 500, 2000, 5000],
            "countries": ALL_COUNTRIES,
            "handles_3ds": True,
            "key_country": "US",
            "blurb": "Adapter pending. Gate is visible but disabled.",
            "enabled": False,
            "sort_hint": 50,
        }
        for processor, label in (("adyen", "Adyen"), ("authnet", "AuthNet"))
    ),
    *(
        {
            "id": f"hit_{processor}",
            "label": f"{label} Hitter",
            "lane": "hit",
            "processor": processor,
            "primitive": "site_flow",
            "amount_cents": 0,
            "capture": "site",
            "cost_cr": Decimal(cost),
            "countries": ALL_COUNTRIES,
            "handles_3ds": False,
            "key_country": "US",
            "blurb": "No live driver captured yet. Gate is visible but disabled.",
            "enabled": False,
            "sort_hint": 50,
        }
        for processor, label, cost in (
            ("stripe", "Stripe", "2"),
            ("jio", "Jio", "5"),
            ("whop", "Whop", "4"),
            ("shopify", "Shopify", "4"),
        )
    ),
)


async def seed_gates(session: AsyncSession) -> int:
    existing = set((await session.scalars(select(Gate.id))).all())
    added = 0
    for payload in GATE_SEED:
        gate_id = str(payload["id"])
        if gate_id in existing:
            continue
        session.add(Gate(**payload))
        session.add(GateStateRow(gate_id=gate_id))
        added += 1
    await session.flush()
    return added
