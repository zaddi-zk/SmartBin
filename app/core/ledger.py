"""Credit ledger (section 16).

One transaction, ``SELECT ... FOR UPDATE`` on the user row, and a unique
``idem_key`` so a retried webhook or a double tap can never debit twice.
Credits never go below zero: an insufficient balance raises before dispatch.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LedgerEntry, User


class InsufficientCredits(Exception):
    def __init__(self, needed: Decimal, balance: Decimal) -> None:
        super().__init__("insufficient credits")
        self.needed = needed
        self.balance = balance


async def debit(
    session: AsyncSession,
    user_id: int,
    delta: Decimal,
    reason: str,
    idem_key: str,
    *,
    check_id: int | None = None,
) -> Decimal:
    """Apply ``delta`` (negative debits, positive grants) and return the balance.

    Idempotent on ``idem_key``: replaying the same key is a no-op that returns
    the current balance.
    """
    existing = await session.scalar(select(LedgerEntry).where(LedgerEntry.idem_key == idem_key))
    if existing is not None:
        balance = await session.scalar(select(User.credits).where(User.id == user_id))
        return Decimal(balance or 0)

    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise LookupError(f"user {user_id} not found")

    balance = Decimal(user.credits)
    new_balance = balance + delta
    if new_balance < 0:
        raise InsufficientCredits(needed=-delta, balance=balance)

    user.credits = new_balance
    session.add(
        LedgerEntry(
            user_id=user_id,
            delta=delta,
            reason=reason,
            check_id=check_id,
            idem_key=idem_key,
        )
    )
    await session.flush()
    return Decimal(user.credits)


async def refund(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    reason: str,
    check_id: int,
) -> Decimal:
    if amount <= 0:
        balance = await session.scalar(select(User.credits).where(User.id == user_id))
        return Decimal(balance or 0)
    return await debit(
        session,
        user_id,
        amount,
        reason,
        f"ref:{check_id}",
        check_id=check_id,
    )
