from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram.types import LabeledPrice

from config import Config

PRODUCTS: dict[str, dict[str, Any]] = {
    "day": {"title": "SMARTBIN 1 day", "description": "24h premium access", "days": 1, "credits": 0},
    "monthly": {"title": "SMARTBIN 30 days", "description": "Monthly premium subscription", "days": 30, "credits": 0},
    "credits500": {"title": "SMARTBIN 500 credits", "description": "500 lookup/check credits", "days": 0, "credits": 500},
}


def price_for(config: Config, product: str) -> int:
    return {
        "day": config.stars_day,
        "monthly": config.stars_month,
        "credits500": config.stars_credits_500,
    }[product]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def create_invoice_link(bot: Any, config: Config, product: str) -> str:
    meta = PRODUCTS[product]
    amount = price_for(config, product)
    kwargs: dict[str, Any] = {}
    if product == "monthly":
        kwargs["subscription_period"] = 2592000
    return await bot.create_invoice_link(
        title=meta["title"],
        description=meta["description"],
        payload=product,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=meta["title"], amount=amount)],
        **kwargs,
    )


async def grant(db: Any, config: Config, user_id: int, product: str, amount_xtr: int) -> dict[str, Any]:
    meta = PRODUCTS.get(product)
    if meta is None:
        return {"granted": False, "reason": "unknown-product"}
    user = await db.get_user(user_id)
    if user is None:
        return {"granted": False, "reason": "no-user"}
    fields: dict[str, Any] = {"stars_total": int(user["stars_total"]) + amount_xtr}
    if meta["credits"]:
        fields["credits"] = int(user["credits"]) + int(meta["credits"])
    if meta["days"]:
        current = _parse(user["premium_until"])
        now = datetime.now(timezone.utc)
        base = current if current and current > now else now
        fields["premium_until"] = _iso(base + timedelta(days=int(meta["days"])))
    await db.update_user(user_id, **fields)
    return {"granted": True, "fields": fields}


async def handle_successful_payment(
    db: Any, config: Config, payment: Any, user_id: int = 0
) -> dict[str, Any]:
    charge_id = str(payment.telegram_payment_charge_id)
    product = str(payment.invoice_payload)
    user_id = int(user_id or (payment.from_user.id if getattr(payment, "from_user", None) else 0))
    amount = int(payment.total_amount)
    inserted = await db.insert_receipt(charge_id, user_id, amount, product)
    if not inserted:
        return {"duplicate": True, "charge_id": charge_id}
    result = await grant(db, config, user_id, product, amount)
    if result.get("granted"):
        first = await db.stats_get("first_payment", 0)
        await db.stats_bump("first_payment")
        if first == 0:
            await db.stats_bump("funnel:first_payment")
    return {"duplicate": False, "charge_id": charge_id, **result}


async def refund(bot: Any, db: Any, user_id: int, charge_id: str) -> dict[str, Any]:
    receipt = await db.get_receipt(charge_id)
    if receipt is None:
        return {"ok": False, "reason": "unknown-receipt"}
    if str(receipt["state"]) == "refunded":
        return {"ok": False, "reason": "already-refunded"}
    await bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
    await db.mark_receipt_refunded(charge_id)
    user = await db.get_user(user_id)
    if user is not None:
        product = str(receipt["product"] or "")
        fields: dict[str, Any] = {}
        if product in ("day", "monthly"):
            fields["premium_until"] = ""
        if product == "credits500":
            fields["credits"] = max(0, int(user["credits"]) - 500)
        if fields:
            await db.update_user(user_id, **fields)
    return {"ok": True, "charge_id": charge_id}
