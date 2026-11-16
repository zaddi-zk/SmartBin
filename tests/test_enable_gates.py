from __future__ import annotations

from app.db import repo
from app.db.models import Gate
from app.db.seed import seed_adyen_keys, seed_authnet_keys, seed_gates
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
