"""3DS measurement (section 17.4).

The probe costs one credit and refunds it whenever the gate itself failed.
The only outcomes are YES, NO and N/A — there is no UNKNOWN path.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from app.bin.measure import MeasureResult, first_3ds_gate, measure_3ds
from app.bin.resolver import BinResolver
from app.config import Settings
from app.core.verdicts import Verdict
from app.db import repo
from app.db.models import Bin3dsPassiveRow, Bin3dsRow, LedgerEntry
from app.routing.router import GateView
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.bin.helpers import gate_view, record


class FakeExecutor:
    def __init__(
        self,
        verdict: Verdict,
        code: str = "",
        gates: list[GateView] | None = None,
    ) -> None:
        self.verdict = verdict
        self.code = code
        self.gates = gates if gates is not None else [gate_view()]
        self.probe_calls = 0

    async def views(self, session: AsyncSession) -> list[GateView]:
        return self.gates

    async def probe(
        self,
        session: AsyncSession,
        *,
        card: Any,
        gate: GateView,
        amount_cents: int = 0,
        capture: bool = False,
    ) -> tuple[Verdict, str]:
        self.probe_calls += 1
        return self.verdict, self.code


def settings(*, canary: str = "4242424242424242|11|29|716") -> Settings:
    return Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        canary_cards=canary,
        bin_timeout_s=2.0,
    )


async def setup_user(session: AsyncSession, credits: str = "10") -> Any:
    user = await repo.ensure_user(session, 1)
    user.credits = Decimal(credits)
    await session.flush()
    return user


def test_first_gate_must_handle_3ds_and_be_routable() -> None:
    gates = [
        gate_view("auth_plain", "Plain Auth", handles_3ds=False),
        gate_view("auth_open", "Open Auth", state="OPEN", handles_3ds=True),
        gate_view("auth_3ds", "3DS Auth", handles_3ds=True),
    ]
    chosen = first_3ds_gate(gates, "US")
    assert chosen is not None
    assert chosen.id == "auth_3ds"


def test_no_3ds_gate_returns_none() -> None:
    gates = [gate_view("auth_plain", "Plain Auth", handles_3ds=False)]
    assert first_3ds_gate(gates, "US") is None


async def test_challenge_is_recorded_and_one_credit_charged(session: AsyncSession) -> None:
    user = await setup_user(session)
    executor = FakeExecutor(Verdict.LIVE_3DS)
    result, detail = await measure_3ds(
        session, user, record(), executor, BinResolver(settings()), settings()
    )
    assert result is MeasureResult.OK
    assert "Challenged" in detail
    assert Decimal(user.credits) == Decimal("9")
    row = await session.get(Bin3dsRow, "44654205")
    assert row is not None
    assert row.probe_reads == 1
    assert row.probe_challenges == 1
    assert row.probe_last_state == "YES"
    assert row.source == "probe"
    assert await session.get(Bin3dsPassiveRow, "44654205") is None


async def test_no_challenge_is_recorded_as_no(session: AsyncSession) -> None:
    user = await setup_user(session)
    executor = FakeExecutor(Verdict.LIVE)
    result, detail = await measure_3ds(
        session, user, record(), executor, BinResolver(settings()), settings()
    )
    assert result is MeasureResult.OK
    assert "No challenge" in detail
    assert Decimal(user.credits) == Decimal("9")
    row = await session.get(Bin3dsRow, "44654205")
    assert row is not None
    assert row.probe_reads == 1
    assert row.probe_challenges == 0
    assert row.probe_last_state == "NO"
    assert await session.get(Bin3dsPassiveRow, "44654205") is None


@pytest.mark.parametrize(
    "verdict",
    [Verdict.KEY_DEAD, Verdict.GATE_FAULT, Verdict.INCONCLUSIVE, Verdict.RETRY],
)
async def test_gate_fault_refunds_and_writes_no_read(
    session: AsyncSession, verdict: Verdict
) -> None:
    user = await setup_user(session)
    executor = FakeExecutor(verdict, "boom")
    result, detail = await measure_3ds(
        session, user, record(), executor, BinResolver(settings()), settings()
    )
    assert result is MeasureResult.ERROR
    assert "refunded" in detail
    assert Decimal(user.credits) == Decimal("10")
    assert await session.get(Bin3dsRow, "44654205") is None
    entries = (await session.scalars(select(LedgerEntry))).all()
    assert [entry.reason for entry in entries] == ["3ds_probe", "refund:3ds_probe"]


async def test_dead_canary_refunds_and_writes_no_read(session: AsyncSession) -> None:
    user = await setup_user(session)
    executor = FakeExecutor(Verdict.DEAD, "card_declined")
    result, detail = await measure_3ds(
        session, user, record(), executor, BinResolver(settings()), settings()
    )
    assert result is MeasureResult.CANARY_DEAD
    assert "refunded" in detail
    assert Decimal(user.credits) == Decimal("10")
    assert await session.get(Bin3dsRow, "44654205") is None
    assert await session.get(Bin3dsPassiveRow, "44654205") is None
    entries = (await session.scalars(select(LedgerEntry))).all()
    assert [entry.reason for entry in entries] == ["3ds_probe", "refund:3ds_probe"]


async def test_free_reprobe_does_not_mint_credits_when_canary_is_dead(
    session: AsyncSession,
) -> None:
    user = await setup_user(session)
    executor = FakeExecutor(Verdict.DEAD, "card_declined")
    bin_resolver = BinResolver(settings())
    first, _ = await measure_3ds(session, user, record(), executor, bin_resolver, settings())
    second, _ = await measure_3ds(session, user, record(), executor, bin_resolver, settings())
    assert first is MeasureResult.CANARY_DEAD
    assert second is MeasureResult.CANARY_DEAD
    assert Decimal(user.credits) == Decimal("10")
    entries = (await session.scalars(select(LedgerEntry))).all()
    assert [entry.reason for entry in entries] == ["3ds_probe", "refund:3ds_probe"]


async def test_no_gate_does_not_charge(session: AsyncSession) -> None:
    user = await setup_user(session)
    executor = FakeExecutor(
        Verdict.LIVE_3DS,
        gates=[gate_view("auth_plain", "Plain Auth", handles_3ds=False)],
    )
    result, _detail = await measure_3ds(
        session, user, record(), executor, BinResolver(settings()), settings()
    )
    assert result is MeasureResult.NO_GATE
    assert executor.probe_calls == 0
    assert Decimal(user.credits) == Decimal("10")


async def test_no_canary_does_not_charge(session: AsyncSession) -> None:
    user = await setup_user(session)
    executor = FakeExecutor(Verdict.LIVE_3DS)
    result, _detail = await measure_3ds(
        session,
        user,
        record(),
        executor,
        BinResolver(settings(canary="")),
        settings(canary=""),
    )
    assert result is MeasureResult.NO_CANARY
    assert executor.probe_calls == 0
    assert Decimal(user.credits) == Decimal("10")


async def test_insufficient_credits_does_not_probe(session: AsyncSession) -> None:
    user = await setup_user(session, credits="0")
    executor = FakeExecutor(Verdict.LIVE_3DS)
    result, _detail = await measure_3ds(
        session, user, record(), executor, BinResolver(settings()), settings()
    )
    assert result is MeasureResult.NO_CREDITS
    assert executor.probe_calls == 0
    assert Decimal(user.credits) == Decimal("0")
