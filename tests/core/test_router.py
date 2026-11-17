from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.db.models import Gate, GateStateRow
from app.routing.router import blocker_until, build_view, route, sibling


def gate(
    gate_id: str,
    *,
    lane: str = "auth",
    countries: list[str] | None = None,
    enabled: bool = True,
) -> Gate:
    return Gate(
        id=gate_id,
        label=gate_id,
        lane=lane,
        processor="stripe",
        primitive="setup_intent",
        amount_cents=0,
        capture="none",
        cost_cr=Decimal("1"),
        refund_on=[],
        countries=countries or ["*"],
        needs_postal=False,
        handles_3ds=True,
        key_country="US",
        ladder=[],
        blurb="",
        enabled=enabled,
        sort_hint=10,
    )


def state(gate_id: str, name: str, p50: int | None = None) -> GateStateRow:
    return GateStateRow(gate_id=gate_id, state=name, p50_ms=p50, samples=1)


def views() -> list:
    return [
        build_view(gate("auth_stripe"), state("auth_stripe", "HEALTHY", 1200)),
        build_view(gate("auth_adyen"), state("auth_adyen", "DEGRADED", 2400)),
        build_view(gate("auth_open"), state("auth_open", "OPEN", 900)),
        build_view(gate("auth_gb", countries=["GB"]), state("auth_gb", "HEALTHY", 1100)),
        build_view(gate("auth_off", enabled=False), state("auth_off", "HEALTHY", 1000)),
    ]


def test_healthy_first_then_degraded() -> None:
    routed = route(views(), "US", "auth")
    assert [view.id for view in routed] == ["auth_stripe", "auth_adyen"]


def test_open_is_excluded_but_visible_in_list() -> None:
    assert "auth_open" not in {view.id for view in route(views(), "US", "auth")}
    assert "auth_open" in {view.id for view in views()}


def test_country_filter() -> None:
    assert "auth_gb" in {view.id for view in route(views(), "GB", "auth")}
    assert "auth_gb" not in {view.id for view in route(views(), "US", "auth")}


def test_unknown_country_does_not_black_out() -> None:
    assert "auth_stripe" in {view.id for view in route(views(), "", "auth")}


def test_sticky_gate_first() -> None:
    routed = route(views(), "US", "auth", gate_id="auth_adyen")
    assert routed[0].id == "auth_adyen"


def test_disabled_gate_never_routed() -> None:
    assert "auth_off" not in {view.id for view in route(views(), "US", "auth")}


def test_gate_without_an_adapter_is_not_ready_or_routed() -> None:
    view = build_view(
        gate("auth_stripe"), state("auth_stripe", "HEALTHY", 1000), installed=()
    )
    assert view.ready is False
    assert route([view], "US", "auth") == []


def test_gate_is_ready_when_its_processor_is_installed() -> None:
    view = build_view(
        gate("auth_stripe"), state("auth_stripe", "HEALTHY", 1000), installed=("stripe",)
    )
    assert view.ready is True
    assert [item.id for item in route([view], "US", "auth")] == ["auth_stripe"]


def test_sibling_is_a_different_gate() -> None:
    other = sibling(views(), "auth", "auth_stripe")
    assert other is not None
    assert other.id != "auth_stripe"


def blocked_state(
    gate_id: str, code: str, expiry: datetime | None
) -> GateStateRow:
    return GateStateRow(
        gate_id=gate_id,
        state="HEALTHY",
        p50_ms=1000,
        samples=3,
        last_err_code=code,
        blocked_until=expiry,
    )


def test_a_processor_block_stops_the_gate_from_routing() -> None:
    # Every tap on a gate the processor refuses is a wasted tap and a scary
    # fault notice, so routing must not offer it at all.
    row = blocked_state("auth_stripe", "raw_card_data_blocked", blocker_until())
    view = build_view(gate("auth_stripe"), row, installed=("stripe",))
    assert view.ready is False
    assert view.blocked == "processor refuses server-side card data"
    assert route([view], "US", "auth") == []


def test_a_stale_block_expires_so_a_fixed_account_recovers() -> None:
    expired = datetime.now(UTC) - timedelta(seconds=1)
    row = blocked_state("auth_stripe", "raw_card_data_blocked", expired)
    view = build_view(gate("auth_stripe"), row, installed=("stripe",))
    assert view.ready is True
    assert view.blocked == ""


def test_an_ordinary_key_failure_is_not_a_permanent_block() -> None:
    # `invalid_api_key` benches a key; the gate itself stays routable so another
    # key in the pool can serve the next check.
    row = blocked_state("auth_stripe", "invalid_api_key", None)
    view = build_view(gate("auth_stripe"), row, installed=("stripe",))
    assert view.ready is True
    assert view.blocked == ""


def test_a_naive_expiry_from_sqlite_does_not_crash_the_router() -> None:
    # SQLite drops tzinfo on DateTime columns. Comparing that naive value to an
    # aware `now` raised TypeError and took /api/health and /api/gates down.
    naive = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)
    row = blocked_state("auth_stripe", "raw_card_data_blocked", naive)
    view = build_view(gate("auth_stripe"), row, installed=("stripe",))
    assert view.ready is False
    assert view.blocked


def test_a_naive_expired_stamp_recovers_the_gate() -> None:
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    row = blocked_state("auth_stripe", "raw_card_data_blocked", naive)
    assert build_view(gate("auth_stripe"), row, installed=("stripe",)).ready is True
