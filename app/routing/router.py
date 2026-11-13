"""Router (section 14). Pure function over gate rows and their state.

Open gates are excluded from routing but stay visible on the status screen.
The sticky gate is tried first; a fault auto-advances to the next candidate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.db.models import Gate, GateStateRow

STATE_RANK = {"HEALTHY": 0, "DEGRADED": 1, "HALF_OPEN": 2, "OPEN": 3}


@dataclass(frozen=True, slots=True)
class GateView:
    id: str
    label: str
    lane: str
    processor: str
    countries: tuple[str, ...]
    cost_cr: Decimal
    amount_cents: int
    ladder: tuple[int, ...]
    state: str
    p50_ms: int | None
    p95_ms: int | None
    success_pct: float | None
    enabled: bool
    handles_3ds: bool
    needs_postal: bool
    key_country: str
    blurb: str
    sort_hint: int

    @property
    def is_open(self) -> bool:
        return self.state == "OPEN"

    @property
    def state_rank(self) -> int:
        return STATE_RANK.get(self.state, 3)


def build_view(gate: Gate, state: GateStateRow | None) -> GateView:
    return GateView(
        id=gate.id,
        label=gate.label,
        lane=gate.lane,
        processor=gate.processor,
        countries=tuple(gate.countries or ()),
        cost_cr=Decimal(gate.cost_cr),
        amount_cents=gate.amount_cents,
        ladder=tuple(gate.ladder or ()),
        state=state.state if state is not None else "HEALTHY",
        p50_ms=state.p50_ms if state is not None else None,
        p95_ms=state.p95_ms if state is not None else None,
        success_pct=float(state.success_pct)
        if state is not None and state.success_pct is not None
        else None,
        enabled=gate.enabled,
        handles_3ds=gate.handles_3ds,
        needs_postal=gate.needs_postal,
        key_country=gate.key_country,
        blurb=gate.blurb,
        sort_hint=gate.sort_hint,
    )


def matches_country(gate: GateView, iso2: str) -> bool:
    if not iso2:
        # Unknown BIN country must not black the user out; try every gate.
        return True
    countries = gate.countries
    return "*" in countries or iso2.upper() in {c.upper() for c in countries}


def route(
    gates: Sequence[GateView],
    card_iso2: str,
    lane: str,
    gate_id: str | None = None,
) -> list[GateView]:
    candidates = [
        gate
        for gate in gates
        if gate.enabled and gate.lane == lane and matches_country(gate, card_iso2)
    ]
    candidates = [gate for gate in candidates if gate.state != "OPEN"]

    if gate_id:
        return sorted(candidates, key=lambda gate: (gate.id != gate_id, gate.state_rank))
    return sorted(candidates, key=lambda gate: (gate.state_rank, gate.p50_ms or 9999))


def sibling(gates: Sequence[GateView], lane: str, exclude: str) -> GateView | None:
    """The retry suggestion on a DEAD card: a different healthy gate."""
    for gate in route(gates, "", lane):
        if gate.id != exclude:
            return gate
    return None
