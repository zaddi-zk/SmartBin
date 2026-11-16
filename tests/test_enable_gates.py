from __future__ import annotations

from app.db import repo
from app.db.models import Gate
from app.db.seed import (
    seed_adyen_keys,
    seed_authnet_keys,
    seed_braintree_keys,
    seed_checkout_keys,
    seed_cybersource_keys,
    seed_gates,
)
from sqlalchemy.ext.asyncio import AsyncSession


async def test_installed_adapter_enables_only_pending_seed_rows(session: AsyncSession) -> None:
    await seed_gates(session)

    adyen = await session.get(Gate, "auth_adyen")
    square = await session.get(Gate, "auth_square")
    assert adyen is not None and adyen.enabled is False
    assert square is not None and square.enabled is False

    changed = await repo.enable_installed_gates(session, {"adyen"})
    await session.flush()

    assert changed >= 1
    adyen = await session.get(Gate, "auth_adyen")
    assert adyen is not None and adyen.enabled is True
    assert adyen.blurb == "Live adapter installed."
    square = await session.get(Gate, "auth_square")
    assert square is not None and square.enabled is False


async def test_enablement_is_idempotent_and_preserves_admin_toggle(session: AsyncSession) -> None:
    await seed_gates(session)
    await repo.enable_installed_gates(session, {"adyen"})
    await session.flush()

    # Second boot: nothing left to migrate.
    assert await repo.enable_installed_gates(session, {"adyen"}) == 0

    # An admin who disables a live gate must stay disabled across restarts.
    adyen = await session.get(Gate, "auth_adyen")
    assert adyen is not None
    adyen.enabled = False
    await session.flush()
    await repo.enable_installed_gates(session, {"adyen"})
    adyen = await session.get(Gate, "auth_adyen")
    assert adyen is not None and adyen.enabled is False


async def test_no_adapters_is_a_noop(session: AsyncSession) -> None:
    await seed_gates(session)
    assert await repo.enable_installed_gates(session, set()) == 0


async def test_adyen_key_seeded_once_per_gate(session: AsyncSession) -> None:
    await seed_gates(session)
    assert await seed_adyen_keys(session, ["adyen_live_key"]) == 3
    assert await seed_adyen_keys(session, ["adyen_live_key"]) == 0


async def test_authnet_key_seeded_once_per_gate(session: AsyncSession) -> None:
    await seed_gates(session)
    assert await seed_authnet_keys(session, ["login|txnkey"]) == 3
    assert await seed_authnet_keys(session, ["login|txnkey"]) == 0


async def test_checkout_key_seeded_once_per_gate(session: AsyncSession) -> None:
    await seed_gates(session)
    assert await seed_checkout_keys(session, ["sk_live_checkout"]) == 1
    assert await seed_checkout_keys(session, ["sk_live_checkout"]) == 0


async def test_cybersource_key_seeded_once_per_gate(session: AsyncSession) -> None:
    await seed_gates(session)
    assert await seed_cybersource_keys(session, ["mid|kid|shared"]) == 3
    assert await seed_cybersource_keys(session, ["mid|kid|shared"]) == 0


async def test_installed_cybersource_adapter_enables_all_its_gates(
    session: AsyncSession,
) -> None:
    await seed_gates(session)
    for gate_id in ("auth_cybersource", "charge_cybersource", "kill_cybersource"):
        gate = await session.get(Gate, gate_id)
        assert gate is not None and gate.enabled is False

    await repo.enable_installed_gates(session, {"cybersource"})
    await session.flush()

    for gate_id in ("auth_cybersource", "charge_cybersource", "kill_cybersource"):
        gate = await session.get(Gate, gate_id)
        assert gate is not None and gate.enabled is True
        assert gate.blurb == "Live adapter installed."


async def test_braintree_key_seeded_once_per_gate(session: AsyncSession) -> None:
    await seed_gates(session)
    assert await seed_braintree_keys(session, ["mid|pub|priv"]) == 3
    assert await seed_braintree_keys(session, ["mid|pub|priv"]) == 0


async def test_installed_braintree_adapter_enables_all_its_gates(
    session: AsyncSession,
) -> None:
    await seed_gates(session)
    for gate_id in ("auth_braintree", "charge_braintree", "kill_braintree"):
        gate = await session.get(Gate, gate_id)
        assert gate is not None and gate.enabled is False

    await repo.enable_installed_gates(session, {"braintree"})
    await session.flush()

    for gate_id in ("auth_braintree", "charge_braintree", "kill_braintree"):
        gate = await session.get(Gate, gate_id)
        assert gate is not None and gate.enabled is True
        assert gate.blurb == "Live adapter installed."


async def test_installed_checkout_adapter_enables_its_gate(session: AsyncSession) -> None:
    await seed_gates(session)
    gate = await session.get(Gate, "charge_checkout")
    assert gate is not None and gate.enabled is False

    await repo.enable_installed_gates(session, {"checkout"})
    await session.flush()

    gate = await session.get(Gate, "charge_checkout")
    assert gate is not None and gate.enabled is True
    assert gate.blurb == "Live adapter installed."
