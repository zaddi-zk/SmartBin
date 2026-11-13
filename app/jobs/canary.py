"""Canary loop (section 12).

Disabled by default: it needs funded canary cards and ideally dedicated keys.
When enabled, it uses a $0 SetupIntent (no money moves), evaluates 3 cards per
gate, and never demotes a gate because one card is bad.
"""

from __future__ import annotations

import asyncio
import random
from decimal import Decimal

import httpx

from app.config import load_settings
from app.core import canary
from app.core.parse import Card, CardError
from app.core.taxonomy import classify
from app.core.verdicts import Verdict
from app.db import repo
from app.db.session import session_scope
from app.runtime import runtime


def parse_canaries(raw: str) -> list[Card]:
    """Canary cards are newline-separated: ``|``, ``;`` and ``,`` belong to the
    card format, so they cannot also separate entries.
    """
    cards: list[Card] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            cards.append(Card.parse(line))
        except CardError:
            continue
    return cards[:3]


async def canary_tick(stop: asyncio.Event) -> None:
    settings = load_settings()
    if not settings.canary_enabled:
        return
    cards = parse_canaries(settings.canary_cards)
    if not cards:
        return

    while not stop.is_set():
        executor = runtime().executor
        async with session_scope() as session:
            for gate in await repo.list_gates(session):
                if not gate.enabled:
                    continue
                adapter = executor.adapters.get(gate.processor)
                if adapter is None:
                    continue
                results: list[Verdict] = []
                for card in cards:
                    key_row = await repo.pick_key(session, gate.id)
                    if key_row is None:
                        break
                    key = executor.keys.to_key(key_row)
                    try:
                        async with httpx.AsyncClient(timeout=settings.check_timeout_s) as client:
                            raw = await adapter.auth(client, key, card, 0, False)
                    except httpx.HTTPError:
                        results.append(Verdict.INCONCLUSIVE)
                        continue
                    verdict, _ = classify(raw.status, raw.body)
                    results.append(canary.as_fault(verdict))
                state = await repo.ensure_gate_state(session, gate.id)
                state.state = canary.evaluate(results)
                state.samples = max(state.samples, len(results))
                if results:
                    healthy = sum(1 for result in results if canary.is_healthy(result))
                    state.success_pct = Decimal(str(round(100 * healthy / len(results), 2)))
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=settings.canary_interval_s + random.randint(0, settings.canary_jitter_s),
            )
        except TimeoutError:
            continue
