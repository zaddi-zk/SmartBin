"""Key lifecycle (section 10): PENDING -> READY -> BENCHED -> DEAD."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repo
from app.db.models import ApiKey, Gate
from app.providers.base import AdapterRegistry, Key


def _now() -> datetime:
    return datetime.now(UTC)


class KeyManager:
    def __init__(self, settings: Settings, adapters: AdapterRegistry) -> None:
        self.settings = settings
        self.adapters = adapters

    @staticmethod
    def to_key(row: ApiKey) -> Key:
        return Key(
            id=row.id,
            gate_id=row.gate_id,
            secret=row.secret,
            country=row.country,
            quota_left=row.quota_left,
            proxy_id=row.proxy_id,
            livemode=row.livemode,
        )

    async def validate_pending(self, session: AsyncSession, client: httpx.AsyncClient) -> int:
        """Promote PENDING keys that pass a livemode read. Returns the count."""
        promoted = 0
        for row in await repo.pending_keys(session):
            gate = await session.get(Gate, row.gate_id)
            if gate is None:
                continue
            adapter = self.adapters.get(gate.processor)
            if adapter is None:
                continue
            ok = await adapter.validate_key(client, self.to_key(row))
            if ok:
                row.state = "READY"
                row.livemode = True
                row.validated_at = _now()
                promoted += 1
            else:
                row.state = "BENCHED"
                row.benched_until = _now() + timedelta(seconds=300)
        return promoted

    async def pick(self, session: AsyncSession, gate_id: str) -> Key | None:
        row = await repo.pick_key(session, gate_id)
        return self.to_key(row) if row is not None else None

    async def bench(self, session: AsyncSession, key_id: int, reason: str) -> None:
        await repo.bench_key(session, key_id, reason, seconds=300)
