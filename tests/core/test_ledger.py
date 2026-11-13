from __future__ import annotations

from decimal import Decimal

import pytest
from app.core.ledger import InsufficientCredits, debit, refund
from app.db import repo
from sqlalchemy.ext.asyncio import AsyncSession


async def test_debit_and_balance(session: AsyncSession) -> None:
    user = await repo.ensure_user(session, 1)
    user.credits = Decimal("10")
    balance = await debit(session, 1, Decimal("-1"), "check:approved", "chk:1")
    assert balance == Decimal("9")


async def test_idempotent_on_key(session: AsyncSession) -> None:
    user = await repo.ensure_user(session, 1)
    user.credits = Decimal("10")
    await debit(session, 1, Decimal("-2"), "check:approved", "chk:7")
    again = await debit(session, 1, Decimal("-2"), "check:approved", "chk:7")
    assert again == Decimal("8")


async def test_insufficient_raises_and_does_not_go_negative(session: AsyncSession) -> None:
    user = await repo.ensure_user(session, 1)
    user.credits = Decimal("1")
    with pytest.raises(InsufficientCredits):
        await debit(session, 1, Decimal("-2"), "check:approved", "chk:9")
    assert Decimal(user.credits) == Decimal("1")


async def test_refund_is_negative_of_debit(session: AsyncSession) -> None:
    user = await repo.ensure_user(session, 1)
    user.credits = Decimal("10")
    await debit(session, 1, Decimal("-1"), "check:approved", "chk:11")
    balance = await refund(session, 1, Decimal("1"), "refund", 11)
    assert balance == Decimal("10")
