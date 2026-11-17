"""Router (section 14). Pure function over gate rows and their state.

Open gates are excluded from routing but stay visible on the status screen.
The sticky gate is tried first; a fault auto-advances to the next candidate.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

from app.db.models import Gate, GateStateRow

STATE_RANK = {"HEALTHY": 0, "DEGRADED": 1, "HALF_OPEN": 2, "OPEN": 3}

# Failures that no retry and no key rotation can fix: the provider refuses this
# account or this request shape. A gate in this state must stop advertising
# itself, because every tap costs the user time and produces nothing.
PERMANENT_BLOCKERS: Final[dict[str, str]] = {
    "raw_card_data_blocked": "processor refuses server-side card data",
    "adapter_missing": "no adapter installed",
}

# How long a permanent blocker is honoured after its last observation. It is
# re-observed by the next attempt, so this only bounds a stale row.
BLOCK_TTL_S = 900


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
    # A gate is ready only when it is enabled *and* its processor adapter is
    # installed, which today means a key is configured. A green gate that
    # cannot execute is a lie the user pays for in taps, so routing never
    # offers one and every screen labels it.
    ready: bool = True
    # Why this gate cannot run at all, when the cause is not a missing key.
    blocked: str = ""

    @property
    def is_open(self) -> bool:
        return self.state == "OPEN"

    @property
    def state_rank(self) -> int:
        return STATE_RANK.get(self.state, 3)


def build_view(
    gate: Gate, state: GateStateRow | None, *, installed: Collection[str] | None = None
) -> GateView:
    """Project a gate row for screens and routing.

    ``installed`` is the set of processor adapters that actually exist in this
    process. Omitting it keeps the old behaviour, which is only safe in tests:
    every production call site passes ``runtime().adapters.names``.
    """
    last_error = (state.last_err_code or "") if state is not None else ""
    blocked = PERMANENT_BLOCKERS.get(last_error, "")
    if blocked and not _blocker_is_fresh(state):
        blocked = ""
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
        # A permanently blocked gate is not ready: offering it only produces
        # a fault notice after the user has spent a tap.
        ready=gate.enabled
        and not blocked
        and (installed is None or gate.processor in installed),
        blocked=blocked,
    )


def blocker_until(now: datetime | None = None) -> datetime:
    """When a just-observed permanent block stops being trusted."""
    return (now or datetime.now(UTC)) + timedelta(seconds=BLOCK_TTL_S)


def _blocker_is_fresh(state: GateStateRow | None) -> bool:
    """Trust a recorded block until its expiry, an admin probe, or its age.

    ``record_sample`` sets ``next_probe_at`` when it records a permanent
    blocker, so this is a real timestamp rather than an inference. The admin
    ``probe`` action clears ``last_err_code`` and re-arms either way.
    """
    if state is None or not state.last_err_code:
        return False
    expiry = state.next_probe_at
    if expiry is None:
        return True
    return datetime.now(UTC) < expiry


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
        if gate.ready and gate.lane == lane and matches_country(gate, card_iso2)
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
