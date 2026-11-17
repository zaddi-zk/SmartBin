from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
from app.config import Settings
from app.core.parse import Card
from app.core.verdicts import Verdict
from app.db import repo
from app.db.models import Bin3dsPassiveRow, Bin3dsRow, BinHitsRow
from app.db.seed import seed_gates
from app.exec.check import CheckExecutor, provider_detail
from app.keys.manager import KeyManager
from app.providers.base import AdapterRegistry, Key, RawResult
from app.proxy.pool import ProxyPool
from sqlalchemy.ext.asyncio import AsyncSession

CARD = Card.parse("4242424242424242|11|29|716")


class FakeAdapter:
    name = "stripe"
    version = "test"

    def __init__(self, result: RawResult | Exception) -> None:
        self.result = result
        self.cancelled: list[str] = []

    async def auth(
        self,
        client: httpx.AsyncClient,
        key: Key,
        card: Card,
        amount_cents: int,
        capture: bool,
    ) -> RawResult:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def cancel(self, client: httpx.AsyncClient, key: Key, ref: str) -> None:
        self.cancelled.append(ref)

    async def capture(
        self, client: httpx.AsyncClient, key: Key, ref: str, amount_cents: int
    ) -> RawResult:
        return RawResult(200, {"status": "succeeded"}, provider_ref=ref)

    async def validate_key(self, client: httpx.AsyncClient, key: Key) -> bool:
        return True


async def setup(session: AsyncSession, adapter: FakeAdapter) -> tuple[Any, CheckExecutor]:
    await seed_gates(session)
    user = await repo.ensure_user(session, 1)
    user.credits = Decimal("10")
    await repo.add_key(session, "auth_stripe", "sk_live_test", "US")
    key = (await repo.keys_for_gate(session, "auth_stripe"))[0]
    key.state = "READY"
    await session.flush()
    settings = Settings(_env_file=None, live_only=False, tg_token="")
    registry = AdapterRegistry({"stripe": adapter})
    executor = CheckExecutor(
        settings, registry, KeyManager(settings, registry), ProxyPool(settings)
    )
    return user, executor


async def test_approved_debits_one(session: AsyncSession) -> None:
    adapter = FakeAdapter(RawResult(200, {"status": "succeeded"}, provider_ref="seti_1"))
    user, executor = await setup(session, adapter)
    outcome = await executor.run(session, user=user, card=CARD, lane="auth")
    assert outcome.verdict is Verdict.APPROVED
    assert outcome.debited == Decimal("1")
    assert outcome.balance == Decimal("9")
    assert outcome.provider_ref == "seti_1"
    passive = await session.get(Bin3dsPassiveRow, CARD.bin8)
    assert passive is not None
    assert passive.reads == 1
    assert passive.challenges == 0
    assert passive.last_state == "NO"
    hits = await session.get(BinHitsRow, CARD.bin8)
    assert hits is not None and hits.attempts == 1 and hits.approved == 1


async def test_retry_is_free_and_never_terminal(session: AsyncSession) -> None:
    adapter = FakeAdapter(RawResult(500, {"error": {"code": "processing_error"}}))
    user, executor = await setup(session, adapter)
    outcome = await executor.run(session, user=user, card=CARD, lane="auth")
    assert outcome.verdict is Verdict.GATE_FAULT
    assert outcome.debited == Decimal("0")
    assert Decimal(user.credits) == Decimal("10")
    # A retry is not a dispatch for the record: no hit and no 3DS evidence.
    assert await session.get(BinHitsRow, CARD.bin8) is None
    assert await session.get(Bin3dsPassiveRow, CARD.bin8) is None


async def test_timeout_is_inconclusive_and_free(session: AsyncSession) -> None:
    adapter = FakeAdapter(httpx.ConnectError("no route"))
    user, executor = await setup(session, adapter)
    outcome = await executor.run(session, user=user, card=CARD, lane="auth")
    assert outcome.verdict is Verdict.GATE_FAULT
    assert outcome.code == "no_bytes"
    assert outcome.debited == Decimal("0")


async def test_3ds_costs_half(session: AsyncSession) -> None:
    adapter = FakeAdapter(
        RawResult(
            402,
            {"error": {"code": "authentication_required"}},
            acs_url="https://acs.example/1",
            three_ds="C",
        )
    )
    user, executor = await setup(session, adapter)
    outcome = await executor.run(session, user=user, card=CARD, lane="auth")
    assert outcome.verdict is Verdict.LIVE_3DS
    assert outcome.debited == Decimal("0.5")
    assert outcome.acs_url == "https://acs.example/1"
    passive = await session.get(Bin3dsPassiveRow, CARD.bin8)
    assert passive is not None
    assert passive.reads == 1
    assert passive.challenges == 1
    assert passive.last_state == "YES"
    assert await session.get(Bin3dsRow, CARD.bin8) is None


async def test_dead_is_terminal_and_free(session: AsyncSession) -> None:
    adapter = FakeAdapter(
        RawResult(402, {"error": {"code": "card_declined", "decline_code": "generic_decline"}})
    )
    user, executor = await setup(session, adapter)
    outcome = await executor.run(session, user=user, card=CARD, lane="auth")
    assert outcome.verdict is Verdict.DEAD
    assert outcome.decline_code == "generic_decline"
    assert outcome.debited == Decimal("0")
    hits = await session.get(BinHitsRow, CARD.bin8)
    assert hits is not None and hits.attempts == 1 and hits.approved == 0
    # A decline is not a 3DS read.
    assert await session.get(Bin3dsPassiveRow, CARD.bin8) is None


async def test_gate_fault_carries_the_provider_message(session: AsyncSession) -> None:
    # The screen and the log must show Stripe's own sentence: the code alone
    # ("bad_request:setup_intent_invalid_parameter") never names the field.
    adapter = FakeAdapter(
        RawResult(
            400,
            {
                "error": {
                    "type": "invalid_request_error",
                    "code": "setup_intent_invalid_parameter",
                    "param": "usage",
                    "message": "You cannot use usage=off_session here.",
                }
            },
        )
    )
    user, executor = await setup(session, adapter)
    outcome = await executor.run(session, user=user, card=CARD, lane="auth")
    assert outcome.verdict is Verdict.GATE_FAULT
    assert outcome.debited == Decimal("0")
    assert outcome.provider_detail == "You cannot use usage=off_session here. (param usage)"


def test_provider_detail_redacts_card_shaped_digits() -> None:
    body = {
        "error": {
            "message": "card 4242424242424242 is invalid",
            "param": "card[number]",
        }
    }
    detail = provider_detail(body)
    assert "4242 4242" not in detail
    assert "4242424242424242" not in detail
    assert "[redacted]" in detail


async def test_gate_state_records_why_a_dispatch_failed(session: AsyncSession) -> None:
    # `gate_fault` on the board is useless to an operator; the recorded reason
    # has to be the provider code so the fix is obvious.
    adapter = FakeAdapter(RawResult(403, {"error": {"code": "raw_card_data_blocked"}}))
    user, executor = await setup(session, adapter)
    await executor.run(session, user=user, card=CARD, lane="auth")
    state = await repo.ensure_gate_state(session, "auth_stripe")
    assert state.last_err_code == "raw_card_data_blocked"
