"""Adapter contract (section 9).

Adapters translate one processor into a ``RawResult``. They never decide a
verdict: ``app.core.taxonomy.classify`` does. That separation is what makes the
taxonomy testable without a network.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx

from app.core.parse import Card


@dataclass(frozen=True, slots=True)
class Key:
    id: int
    gate_id: str
    secret: str
    country: str
    quota_left: int | None = None
    proxy_id: int | None = None
    livemode: bool = False


@dataclass(slots=True)
class RawResult:
    status: int
    body: Mapping[str, object] = field(default_factory=dict)
    provider_ref: str = ""
    latency_ms: int = 0
    acs_url: str = ""
    avs: str = ""
    cvc: str = ""
    three_ds: str = ""
    raw_text: str = ""


@runtime_checkable
class Adapter(Protocol):
    name: str
    version: str

    async def auth(
        self,
        client: httpx.AsyncClient,
        key: Key,
        card: Card,
        amount_cents: int,
        capture: bool,
    ) -> RawResult: ...

    async def cancel(self, client: httpx.AsyncClient, key: Key, ref: str) -> None: ...

    async def capture(
        self, client: httpx.AsyncClient, key: Key, ref: str, amount_cents: int
    ) -> RawResult: ...

    async def validate_key(self, client: httpx.AsyncClient, key: Key) -> bool: ...


class AdapterRegistry:
    """Maps ``gate.processor`` to an adapter instance."""

    def __init__(self, adapters: Mapping[str, Adapter] | None = None) -> None:
        self._adapters: dict[str, Adapter] = dict(adapters or {})

    def register(self, adapter: Adapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, processor: str) -> Adapter | None:
        return self._adapters.get(processor)

    def require(self, processor: str) -> Adapter:
        adapter = self.get(processor)
        if adapter is None:
            raise LookupError(f"no adapter for processor {processor!r}")
        return adapter

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))
