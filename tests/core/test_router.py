from __future__ import annotations

from decimal import Decimal

from app.db.models import Gate, GateStateRow
from app.routing.router import build_view, route, sibling


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


def test_sibling_is_a_different_gate() -> None:
    other = sibling(views(), "auth", "auth_stripe")
    assert other is not None
    assert other.id != "auth_stripe"
