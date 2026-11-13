"""Kill ladder writes the same passive 3DS evidence as a check (section 17.4)."""

from __future__ import annotations

from decimal import Decimal

from app.core.verdicts import Verdict
from app.db import repo
from app.db.models import Bin3dsPassiveRow, BinHitsRow
from app.exec.kill import KillExecutor
from app.providers.base import RawResult
from sqlalchemy.ext.asyncio import AsyncSession

from tests.core.test_executor import CARD, FakeAdapter, setup


async def _arm_kill(session: AsyncSession) -> None:
    await repo.add_key(session, "kill_stripe", "sk_live_kill", "US")
    key = (await repo.keys_for_gate(session, "kill_stripe"))[0]
    key.state = "READY"
    await session.flush()


async def test_kill_3ds_writes_passive_read_and_one_hit(session: AsyncSession) -> None:
    adapter = FakeAdapter(
        RawResult(
            402,
            {"error": {"code": "authentication_required"}},
            acs_url="https://acs.example/1",
            three_ds="C",
        )
    )
    user, executor = await setup(session, adapter)
    await _arm_kill(session)
    outcome = await KillExecutor(executor).run(session, user=user, card=CARD, bin_iso2="US")
    assert outcome.verdict is Verdict.LIVE_3DS
    assert outcome.debited == Decimal("6")

    passive = await session.get(Bin3dsPassiveRow, CARD.bin8)
    assert passive is not None
    assert passive.reads == 1
    assert passive.challenges == 1
    assert passive.last_state == "YES"

    hits = await session.get(BinHitsRow, CARD.bin8)
    assert hits is not None
    assert hits.attempts == 1
    assert hits.approved == 1


async def test_kill_retry_writes_no_passive_read_and_no_hit(session: AsyncSession) -> None:
    adapter = FakeAdapter(RawResult(500, {"error": {"code": "processing_error"}}))
    user, executor = await setup(session, adapter)
    await _arm_kill(session)
    outcome = await KillExecutor(executor).run(session, user=user, card=CARD, bin_iso2="US")
    assert outcome.verdict is Verdict.RETRY
    assert await session.get(Bin3dsPassiveRow, CARD.bin8) is None
    assert await session.get(BinHitsRow, CARD.bin8) is None
