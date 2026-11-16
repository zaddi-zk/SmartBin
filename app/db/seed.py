"""Idempotent gate seeding.

Only gates whose adapter is actually installed are enabled. A seeded-but-off
gate still renders in the lane list (dim, tappable) so the user never sees a
blank screen, but the router will not send a card to it.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, Gate, GateStateRow

ALL_COUNTRIES = ["*"]

# Gates that consume a Stripe account key. One live key serves all three lanes.
STRIPE_KEY_GATES: tuple[str, ...] = (
    "auth_stripe",
    "charge_stripe",
    "kill_stripe",
)

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


async def seed_stripe_keys(session: AsyncSession, secrets: list[str]) -> int:
    """Insert unseen env keys as PENDING; ``validate_pending`` promotes them.

    Idempotent: a secret already present for a gate is never re-inserted, so
    restarting the service cannot duplicate keys. Invalid secrets stay PENDING
    only until validation bounces them to BENCHED — they never reach READY.
    """
    return await _seed_keys(session, STRIPE_KEY_GATES, secrets)


ADYEN_KEY_GATES: tuple[str, ...] = ("auth_adyen", "charge_adyen", "kill_adyen")
AUTHNET_KEY_GATES: tuple[str, ...] = ("auth_authnet", "charge_authnet", "kill_authnet")


async def seed_adyen_keys(session: AsyncSession, secrets: list[str]) -> int:
    """Same lifecycle as Stripe; one Adyen key serves auth/charge/kill."""
    return await _seed_keys(session, ADYEN_KEY_GATES, secrets)


async def seed_authnet_keys(session: AsyncSession, secrets: list[str]) -> int:
    """One Authorize.Net login/transaction-key pair serves all three lanes."""
    return await _seed_keys(session, AUTHNET_KEY_GATES, secrets)


async def _seed_keys(
    session: AsyncSession, gate_ids: tuple[str, ...], secrets: list[str]
) -> int:
    added = 0
    for gate_id in gate_ids:
        if (await session.get(Gate, gate_id)) is None:
            continue
        known = set(
            (
                await session.scalars(
                    select(ApiKey.secret).where(ApiKey.gate_id == gate_id)
                )
            ).all()
        )
        for secret in secrets:
            if secret in known:
                continue
            known.add(secret)
            session.add(ApiKey(gate_id=gate_id, secret=secret, state="PENDING"))
            added += 1
    await session.flush()
    return added
