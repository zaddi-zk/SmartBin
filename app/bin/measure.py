"""3DS probe (section 17.4).

3DS is measured, never inferred. One canary card, one 3DS-capable gate, one
credit — refunded if the gate itself faulted. The only states this can produce
are YES, NO and N/A; the word UNKNOWN does not exist here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bin.model import BinRecord
from app.config import Settings
from app.core import ledger
from app.core.parse import Card, CardError
from app.core.verdicts import Verdict, is_fault
from app.db import repo
from app.db.models import LedgerEntry, User
from app.routing.router import GateView, route

if TYPE_CHECKING:
    from app.bin.resolver import BinResolver
    from app.exec.check import CheckExecutor

PROBE_COST = Decimal("1")


class MeasureResult(Enum):
    OK = "ok"
    NO_GATE = "no_gate"
    NO_CANARY = "no_canary"
    NO_CREDITS = "no_credits"
    CANARY_DEAD = "canary_dead"
    ERROR = "error"


def first_3ds_gate(views: list[GateView], iso2: str | None) -> GateView | None:
    for gate in route(views, iso2 or "", "auth"):
        if gate.handles_3ds:
            return gate
    return None


def _parse_lines(raw: str) -> list[Card]:
    cards: list[Card] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            cards.append(Card.parse(line))
        except CardError:
            continue
    return cards


async def canary_card_for(
    session: AsyncSession, iso2: str | None, settings: Settings
) -> Card | None:
    rows = await repo.canary_cards(session, iso2 or "")
    if not rows:
        rows = await repo.canary_cards(session)
    for row in rows:
        try:
            return Card.parse(f"{row.pan_enc}|{row.mm}|{row.yy}|{row.cvv_enc}|{row.postal}")
        except CardError:
            continue
    cards = _parse_lines(settings.canary_cards)
    return cards[0] if cards else None


def _hour() -> int:
    return int(datetime.now(UTC).timestamp() // 3600)


async def measure_3ds(
    session: AsyncSession,
    user: User,
    record: BinRecord,
    executor: CheckExecutor,
    resolver: BinResolver,
    settings: Settings,
) -> tuple[MeasureResult, str]:
    gate = first_3ds_gate(await executor.views(session), record.iso2)
    if gate is None:
        return MeasureResult.NO_GATE, "No 3DS-capable auth gate for this country."

    card = await canary_card_for(session, record.iso2, settings)
    if card is None:
        return MeasureResult.NO_CANARY, "No canary card configured for this country."

    idem = f"p3ds:{record.bin}:{_hour()}"
    charged = (
        await session.scalar(select(LedgerEntry.id).where(LedgerEntry.idem_key == idem))
        is None
    )
    try:
        await ledger.debit(session, user.id, -PROBE_COST, "3ds_probe", idem)
    except ledger.InsufficientCredits:
        return MeasureResult.NO_CREDITS, "A 3DS measurement costs 1 cr."

    verdict, code = await executor.probe(
        session, card=card, gate=gate, amount_cents=0, capture=False
    )
    if verdict is Verdict.DEAD:
        # The canary itself is dead. That is not evidence about this BIN, so
        # refund and write nothing. A free re-probe must not mint credits.
        if charged:
            await ledger.debit(
                session, user.id, PROBE_COST, "refund:3ds_probe", f"ref:{idem}"
            )
        return (
            MeasureResult.CANARY_DEAD,
            "Canary card is dead; probe skipped and 1 cr refunded.",
        )
    if verdict is Verdict.KEY_DEAD or is_fault(verdict):
        if charged:
            await ledger.debit(
                session, user.id, PROBE_COST, "refund:3ds_probe", f"ref:{idem}"
            )
        return MeasureResult.ERROR, f"Gate faulted ({code}); 1 cr refunded."

    challenged = verdict is Verdict.LIVE_3DS
    await resolver.record_probe_3ds(session, record.bin, challenged=challenged)
    if challenged:
        return MeasureResult.OK, f"Challenged on {gate.label}."
    return MeasureResult.OK, f"No challenge on {gate.label}."
