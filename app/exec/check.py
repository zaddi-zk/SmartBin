"""Executor (section 15).

One place decides whether credits move: ``CHARGE[verdict] * gate.cost_cr`` and
nothing else. A timeout, a dead key, an open gate and a missing proxy all cost
zero by construction.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core import ledger
from app.core.breaker import Breaker, BreakerState
from app.core.parse import Card
from app.core.taxonomy import classify, extract_decline_code
from app.core.verdicts import Verdict, charge_for, is_fault, is_terminal
from app.db import repo
from app.db.models import User
from app.keys.manager import KeyManager
from app.logging import log_check
from app.providers.base import Adapter, AdapterRegistry, Key, RawResult
from app.proxy.pool import ProxyPool
from app.routing.router import GateView, build_view, route

NO_BYTES = 599


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Attempt:
    gate_id: str
    label: str
    verdict: Verdict
    code: str = ""
    decline_code: str = ""
    latency_ms: int = 0
    provider_ref: str = ""
    acs_url: str = ""
    avs: str = ""
    cvc: str = ""
    three_ds: str = ""
    raw_error: str = ""


@dataclass(slots=True)
class CheckOutcome:
    verdict: Verdict
    gate_id: str
    gate_label: str
    code: str = ""
    decline_code: str = ""
    latency_ms: int = 0
    provider_ref: str = ""
    acs_url: str = ""
    avs: str = ""
    cvc: str = ""
    three_ds: str = ""
    pan_masked: str = ""
    amount_cents: int = 0
    debited: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")
    check_id: int | None = None
    reason: str = ""
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return is_terminal(self.verdict)


class CheckExecutor:
    def __init__(
        self,
        settings: Settings,
        adapters: AdapterRegistry,
        keys: KeyManager,
        proxies: ProxyPool,
        breakers: dict[str, Breaker] | None = None,
    ) -> None:
        self.settings = settings
        self.adapters = adapters
        self.keys = keys
        self.proxies = proxies
        self.breakers: dict[str, Breaker] = breakers or {}
        # Section 22: at most 3 concurrent provider calls per process.
        self.semaphore = asyncio.Semaphore(max(1, settings.rate_concurrent))

    def breaker(self, gate_id: str) -> Breaker:
        breaker = self.breakers.get(gate_id)
        if breaker is None:
            breaker = Breaker(
                threshold=self.settings.breaker_fault_threshold,
                open_s=self.settings.breaker_open_s,
                backoff_cap_s=self.settings.breaker_backoff_cap_s,
            )
            self.breakers[gate_id] = breaker
        return breaker

    async def views(self, session: AsyncSession) -> list[GateView]:
        gates = await repo.list_gates(session)
        states = await repo.gate_states(session)
        return [build_view(gate, states.get(gate.id)) for gate in gates]

    async def probe(
        self,
        session: AsyncSession,
        *,
        card: Card,
        gate: GateView,
        amount_cents: int = 0,
        capture: bool = False,
    ) -> tuple[Verdict, str]:
        """One canary authorization for the 3DS measurement (section 17.4).

        This is not a user check: it writes no ``checks`` row and no
        ``bin_hits`` row. The caller owns the 1-cr probe debit.
        """
        adapter = self.adapters.get(gate.processor)
        if adapter is None:
            return Verdict.GATE_FAULT, "adapter_missing"

        key_row = await repo.pick_key(session, gate.id)
        if key_row is None:
            await self.fault(session, gate.id, "no_key")
            return Verdict.GATE_FAULT, "no_key"

        key = self.keys.to_key(key_row)
        proxy_url = await self.proxies.bind(session, key_row, key)
        if proxy_url is None and self.settings.require_proxy:
            await self.keys.bench(session, key.id, "no_egress")
            await self.fault(session, gate.id, "no_egress")
            return Verdict.GATE_FAULT, "no_egress"

        raw, latency = await self._call(
            adapter,
            key,
            card,
            amount_cents,
            capture=capture,
            proxy_url=proxy_url,
        )
        verdict, code = self._classify(raw)
        log_check(
            user_id=0,
            gate_id=gate.id,
            key_id=key.id,
            proxy_id=key.proxy_id,
            status=raw.status,
            code=code,
            decline_code=extract_decline_code(raw.body),
            provider_ref=raw.provider_ref,
            api_version=adapter.version,
            latency_ms=latency,
            verdict=verdict.value,
            lane="3ds_probe",
        )
        await self.record_sample(session, gate.id, latency, verdict)
        return verdict, code

    async def run(
        self,
        session: AsyncSession,
        *,
        user: User,
        card: Card,
        lane: str,
        gate_id: str | None = None,
        bin_iso2: str = "",
        amount_cents: int | None = None,
    ) -> CheckOutcome:
        candidates = route(await self.views(session), bin_iso2, lane, gate_id)
        if not candidates:
            return CheckOutcome(
                verdict=Verdict.GATE_FAULT,
                gate_id="",
                gate_label="",
                code="gate_unavailable",
                reason="no gate for this lane/country",
                pan_masked=card.masked,
            )

        attempts: list[Attempt] = []
        chosen: Attempt | None = None
        chosen_gate: GateView | None = None
        chosen_key: Key | None = None
        chosen_proxy: str | None = None
        last_code = "gate_fault"

        for gate in candidates[:3]:
            adapter = self.adapters.get(gate.processor)
            if adapter is None:
                attempts.append(
                    Attempt(gate.id, gate.label, Verdict.GATE_FAULT, code="adapter_missing")
                )
                continue

            cost = Decimal(gate.cost_cr)
            if Decimal(user.credits) < cost:
                raise ledger.InsufficientCredits(cost, Decimal(user.credits))

            key_row = await repo.pick_key(session, gate.id)
            if key_row is None:
                attempts.append(Attempt(gate.id, gate.label, Verdict.GATE_FAULT, code="no_key"))
                await self.fault(session, gate.id, "no_key")
                continue

            key = self.keys.to_key(key_row)
            proxy_url = await self.proxies.bind(session, key_row, key)
            if proxy_url is None and self.settings.require_proxy:
                await self.keys.bench(session, key.id, "no_egress")
                attempts.append(Attempt(gate.id, gate.label, Verdict.GATE_FAULT, code="no_egress"))
                await self.fault(session, gate.id, "no_egress")
                continue

            amount = amount_cents if amount_cents is not None else gate.amount_cents
            raw, latency = await self._call(
                adapter,
                key,
                card,
                amount,
                capture=gate.lane != "auth",
                proxy_url=proxy_url,
            )
            verdict, code = self._classify(raw)
            log_check(
                user_id=user.id,
                gate_id=gate.id,
                key_id=key.id,
                proxy_id=key.proxy_id,
                status=raw.status,
                code=code,
                decline_code=extract_decline_code(raw.body),
                provider_ref=raw.provider_ref,
                api_version=adapter.version,
                latency_ms=latency,
                verdict=verdict.value,
            )
            # Passive, authoritative 3DS evidence. Only a real dispatch with a
            # terminal 3DS answer counts; RETRY/KEY_DEAD/GATE_FAULT/INCONCLUSIVE
            # are not evidence and never reach this branch.
            if verdict in (Verdict.LIVE_3DS, Verdict.APPROVED):
                await repo.bump_passive_3ds(
                    session, card.bin8, challenged=verdict is Verdict.LIVE_3DS
                )
            attempt = Attempt(
                gate_id=gate.id,
                label=gate.label,
                verdict=verdict,
                code=code,
                decline_code=extract_decline_code(raw.body),
                latency_ms=latency,
                provider_ref=raw.provider_ref,
                acs_url=raw.acs_url,
                avs=raw.avs,
                cvc=raw.cvc,
                three_ds=self._three_ds(verdict, raw),
                raw_error=str(raw.body)[:300],
            )
            attempts.append(attempt)
            await self.record_sample(session, gate.id, latency, verdict)

            if verdict is Verdict.KEY_DEAD:
                await self.keys.bench(session, key.id, code or "key_dead")
                continue
            if verdict in (Verdict.RETRY, Verdict.INCONCLUSIVE):
                last_code = code
                continue

            chosen, chosen_gate, chosen_key, chosen_proxy = attempt, gate, key, proxy_url
            break

        if chosen is None or chosen_gate is None or chosen_key is None:
            return CheckOutcome(
                verdict=Verdict.GATE_FAULT,
                gate_id=attempts[-1].gate_id if attempts else "",
                gate_label=attempts[-1].label if attempts else "",
                code=last_code,
                reason="every candidate gate faulted; 0 cr charged",
                pan_masked=card.masked,
                attempts=attempts,
            )

        amount = amount_cents if amount_cents is not None else chosen_gate.amount_cents
        if chosen_gate.lane == "charge" and chosen.verdict is Verdict.APPROVED:
            chosen = await self._settle(chosen, chosen_gate, chosen_key, amount, chosen_proxy)

        approved = chosen.verdict in (Verdict.APPROVED, Verdict.LIVE, Verdict.LIVE_3DS)
        # Once per card dispatch, and never for a retry, dead key or gate fault.
        if chosen.verdict is not Verdict.KEY_DEAD and not is_fault(chosen.verdict):
            await repo.bump_bin_hits(session, card.bin8, approved=approved, user_id=user.id)

        cost = Decimal(chosen_gate.cost_cr) * charge_for(chosen.verdict)
        check = await repo.create_check(
            session,
            user_id=user.id,
            gate_id=chosen_gate.id,
            pan_masked=card.masked,
            pan_hash=card.pan_hash,
            verdict=chosen.verdict.value,
            code=chosen.code,
            decline_code=chosen.decline_code,
            avs=chosen.avs,
            cvc=chosen.cvc,
            three_ds=chosen.three_ds,
            latency_ms=chosen.latency_ms,
            provider_ref=chosen.provider_ref,
            amount_cents=amount,
            debited=Decimal("0"),
        )

        debited = Decimal("0")
        balance = Decimal(user.credits)
        if cost > 0:
            balance = await ledger.debit(
                session,
                user.id,
                -cost,
                f"check:{chosen.verdict.value.lower()}",
                f"chk:{check.id}",
                check_id=check.id,
            )
            debited = cost
            check.debited = debited
        if not is_terminal(chosen.verdict) and debited > 0 and self.settings.credit_refund_on_error:
            balance = await ledger.refund(
                session, user.id, debited, "refund:non_terminal", check.id
            )
            check.refunded = True
            check.debited = Decimal("0")
            debited = Decimal("0")

        return CheckOutcome(
            verdict=chosen.verdict,
            gate_id=chosen_gate.id,
            gate_label=chosen_gate.label,
            code=chosen.code,
            decline_code=chosen.decline_code,
            latency_ms=chosen.latency_ms,
            provider_ref=chosen.provider_ref,
            acs_url=chosen.acs_url,
            avs=chosen.avs,
            cvc=chosen.cvc,
            three_ds=chosen.three_ds,
            pan_masked=card.masked,
            amount_cents=amount,
            debited=debited,
            balance=balance,
            check_id=check.id,
            attempts=attempts,
        )

    async def _settle(
        self,
        attempt: Attempt,
        gate: GateView,
        key: Key,
        amount: int,
        proxy_url: str | None,
    ) -> Attempt:
        adapter = self.adapters.require(gate.processor)
        started = time.perf_counter()
        try:
            async with self.semaphore, httpx.AsyncClient(
                proxy=proxy_url, timeout=self.settings.check_timeout_s, http2=False
            ) as client:
                raw = await adapter.capture(client, key, attempt.provider_ref, amount)
        except httpx.HTTPError:
            await self._cancel(adapter, key, attempt.provider_ref, proxy_url)
            attempt.verdict = Verdict.RETRY
            attempt.code = "capture_no_bytes"
            return attempt
        latency = int((time.perf_counter() - started) * 1000)
        verdict, code = self._classify(raw)
        if verdict is not Verdict.APPROVED:
            # Never leave a manual-capture hold behind when the capture fails.
            await self._cancel(adapter, key, attempt.provider_ref, proxy_url)
            attempt.verdict = verdict
            attempt.code = code
        attempt.latency_ms += latency
        return attempt

    async def _cancel(
        self, adapter: Adapter, key: Key, ref: str, proxy_url: str | None
    ) -> None:
        if not ref:
            return
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url, timeout=self.settings.check_timeout_s, http2=False
            ) as client:
                await adapter.cancel(client, key, ref)
        except httpx.HTTPError:
            return

    async def _call(
        self,
        adapter: Adapter,
        key: Key,
        card: Card,
        amount: int,
        *,
        capture: bool,
        proxy_url: str | None,
    ) -> tuple[RawResult, int]:
        started = time.perf_counter()
        try:
            async with self.semaphore, httpx.AsyncClient(
                proxy=proxy_url,
                timeout=self.settings.check_timeout_s,
                http2=False,
            ) as client:
                raw = await adapter.auth(client, key, card, amount, capture)
        except httpx.HTTPError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return (
                RawResult(
                    status=NO_BYTES,
                    body={"error": {"code": "no_bytes", "message": str(exc)[:200]}},
                    raw_text=str(exc)[:300],
                ),
                elapsed,
            )
        return raw, int((time.perf_counter() - started) * 1000)

    @staticmethod
    def _classify(raw: RawResult) -> tuple[Verdict, str]:
        if raw.status == NO_BYTES:
            return Verdict.INCONCLUSIVE, "no_bytes"
        return classify(raw.status, raw.body)

    @staticmethod
    def _three_ds(verdict: Verdict, raw: RawResult) -> str:
        if verdict is Verdict.LIVE_3DS:
            return "C"
        if verdict is Verdict.APPROVED:
            return "N"
        return raw.three_ds

    async def record_sample(
        self, session: AsyncSession, gate_id: str, latency_ms: int, verdict: Verdict
    ) -> None:
        ok = verdict in (Verdict.APPROVED, Verdict.LIVE, Verdict.LIVE_3DS, Verdict.DEAD)
        health = await repo.load_health(session, gate_id)
        health.add(latency_ms, ok)
        await repo.add_sample(session, gate_id, latency_ms, ok)
        breaker = self.breaker(gate_id)
        breaker.on_result(verdict)
        state = await repo.ensure_gate_state(session, gate_id)
        state.state = self._state_name(breaker.state)
        state.faults = breaker.faults
        state.opened_at = breaker.opened_at
        state.backoff_s = breaker.backoff_s
        state.p50_ms = health.p50()
        state.p95_ms = health.p95()
        success = health.success_pct()
        state.success_pct = Decimal(str(success)) if success is not None else None
        state.samples = health.total
        state.last_err_code = None if ok else verdict.value.lower()
        if ok:
            state.last_ok_at = _now()

    async def fault(self, session: AsyncSession, gate_id: str, code: str) -> None:
        """A pre-dispatch fault (no key, no egress) counts against the gate, not latency."""
        breaker = self.breaker(gate_id)
        breaker.on_result(Verdict.GATE_FAULT)
        state = await repo.ensure_gate_state(session, gate_id)
        state.state = self._state_name(breaker.state)
        state.faults = breaker.faults
        state.opened_at = breaker.opened_at
        state.backoff_s = breaker.backoff_s
        state.last_err_code = code

    @staticmethod
    def _state_name(state: BreakerState) -> str:
        if state is BreakerState.OPEN:
            return "OPEN"
        if state is BreakerState.HALF_OPEN:
            return "HALF_OPEN"
        return "HEALTHY"
