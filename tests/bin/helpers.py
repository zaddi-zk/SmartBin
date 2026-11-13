"""Shared fixtures for the section 17 tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.bin.model import BinRecord, HitStat, ThreeDSStat
from app.routing.router import GateView


def gate_view(
    gate_id: str = "auth_stripe",
    label: str = "Stripe Auth",
    *,
    lane: str = "auth",
    state: str = "HEALTHY",
    cost: str = "1",
    p50: int | None = 1200,
    handles_3ds: bool = True,
    enabled: bool = True,
) -> GateView:
    return GateView(
        id=gate_id,
        label=label,
        lane=lane,
        processor="stripe",
        countries=("*",),
        cost_cr=Decimal(cost),
        amount_cents=0,
        ladder=(),
        state=state,
        p50_ms=p50,
        p95_ms=None,
        success_pct=None,
        enabled=enabled,
        handles_3ds=handles_3ds,
        needs_postal=False,
        key_country="US",
        blurb="",
        sort_hint=10,
    )


def record(
    *,
    bin_: str = "44654205",
    sources_n: int = 2,
    conflicts: int = 0,
    fetched_at: datetime | None = None,
    reads: int = 0,
    challenges: int = 0,
    last_state: str = "",
    attempts: int = 0,
    approved: int = 0,
    type_: str | None = "credit",
    prepaid: bool | None = False,
    commercial: bool | None = False,
    debit: bool | None = False,
) -> BinRecord:
    now = datetime.now(UTC)
    flags: list[str] = []
    if sources_n < 2:
        flags.append("single source")
    if conflicts:
        flags.append(f"{conflicts} conflict" + ("s" if conflicts > 1 else ""))
    return BinRecord(
        bin=bin_,
        parent6=bin_[:6],
        brand="visa",
        type=type_,
        tier="platinum" if type_ else None,
        prepaid=prepaid,
        commercial=commercial,
        debit=debit,
        bank_short="Wells Fargo Bank, N.A.",
        bank_legal="Wells Fargo Bank, National Association",
        bank_phone="1-800-869-3557",
        bank_site="wellsfargo.com",
        country="United States",
        iso2="US",
        currency="USD",
        pan_len=16,
        range_lo=None,
        range_hi=None,
        sources_n=sources_n,
        conflicts=conflicts,
        flags=flags,
        fetched_at=fetched_at or now,
        ttl_days=7,
        r3ds=ThreeDSStat(
            reads=reads,
            challenges=challenges,
            last_at=now - timedelta(hours=3) if reads else None,
            last_state=last_state,
        ),
        hits=HitStat(
            attempts=attempts,
            approved=approved,
            last_hit_at=now - timedelta(hours=2) if attempts else None,
        ),
    )
