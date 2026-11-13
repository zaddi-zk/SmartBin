"""Kill ladder (section 15).

Escalating manual-capture attempts. Stops on the first approve and cancels the
hold so nothing settles. A gate fault stops the ladder; a dead key benches the
key and continues.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ledger
from app.core.parse import Card
from app.core.taxonomy import classify, extract_decline_code
from app.core.verdicts import Verdict, is_terminal
from app.db import repo
from app.db.models import User
from app.exec.check import NO_BYTES, CheckExecutor
from app.logging import log_check
from app.providers.base import Adapter, Key, RawResult
from app.routing.router import route


@dataclass(slots=True)
class KillRung:
    amount_cents: int
    verdict: Verdict
    code: str = ""
    decline_code: str = ""
    latency_ms: int = 0
    provider_ref: str = ""
    debited: Decimal = Decimal("0")


@dataclass(slots=True)
class KillOutcome:
    verdict: Verdict
    gate_id: str
    gate_label: str
    reached_cents: int = 0
    debited: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")
    pan_masked: str = ""
    reason: str = ""
    rungs: list[KillRung] = field(default_factory=list)


class KillExecutor:
    def __init__(self, executor: CheckExecutor) -> None:
        self.executor = executor

    async def run(
        self,
        session: AsyncSession,
        *,
        user: User,
        card: Card,
        gate_id: str | None = None,
        bin_iso2: str = "",
        max_rungs: int = 5,
    ) -> KillOutcome:
        views = await self.executor.views(session)
        candidates = route(views, bin_iso2, "kill", gate_id)
        if not candidates:
            return KillOutcome(
                verdict=Verdict.GATE_FAULT,
                gate_id="",
                gate_label="",
                reason="no kill gate available",
                pan_masked=card.masked,
            )

        gate = candidates[0]
        adapter = self.executor.adapters.get(gate.processor)
        if adapter is None:
            return KillOutcome(
                verdict=Verdict.GATE_FAULT,
                gate_id=gate.id,
                gate_label=gate.label,
                reason="kill adapter not installed",
                pan_masked=card.masked,
            )

        ladder = list(gate.ladder or ())[:max_rungs]
        if not ladder:
            return KillOutcome(
                verdict=Verdict.GATE_FAULT,
                gate_id=gate.id,
                gate_label=gate.label,
                reason="gate has no ladder",
                pan_masked=card.masked,
            )

        cost = Decimal(gate.cost_cr)
        rungs: list[KillRung] = []
        total = Decimal("0")
        balance = Decimal(user.credits)
        best: Verdict | None = None
        reason = "ladder exhausted"

        for amount in ladder:
            if Decimal(user.credits) - total < cost:
                reason = "insufficient balance"
                break

            key_row = await repo.pick_key(session, gate.id)
            if key_row is None:
                await self.executor.fault(session, gate.id, "no_key")
                reason = "no key"
                best = Verdict.GATE_FAULT
                break

            key: Key = self.executor.keys.to_key(key_row)
            proxy_url = await self.executor.proxies.bind(session, key_row, key)
            if proxy_url is None and self.executor.settings.require_proxy:
                await self.executor.keys.bench(session, key.id, "no_egress")
                reason = "no egress"
                best = Verdict.GATE_FAULT
                break

            raw, latency = await self._call(adapter, key, card, amount, proxy_url)
            verdict, code = (
                (Verdict.INCONCLUSIVE, "no_bytes")
                if raw.status == NO_BYTES
                else classify(raw.status, raw.body)
            )
            rung = KillRung(
                amount_cents=amount,
                verdict=verdict,
                code=code,
                decline_code=extract_decline_code(raw.body),
                latency_ms=latency,
                provider_ref=raw.provider_ref,
            )
            rungs.append(rung)
            await self.executor.record_sample(session, gate.id, latency, verdict)
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
                lane="kill",
                amount_cents=amount,
            )

            if verdict is Verdict.KEY_DEAD:
                await self.executor.keys.bench(session, key.id, code or "key_dead")
                reason = "key benched"
                best = Verdict.KEY_DEAD
                continue

            if verdict is Verdict.APPROVED:
                async with httpx.AsyncClient(
                    proxy=proxy_url,
                    timeout=self.executor.settings.check_timeout_s,
                    http2=False,
                ) as cancel_client:
                    await adapter.cancel(cancel_client, key, raw.provider_ref)
                debit = cost
                total += debit
                rung.debited = debit
                reason = "approved"
                best = Verdict.APPROVED
                break

            if verdict in (Verdict.GATE_FAULT, Verdict.INCONCLUSIVE, Verdict.RETRY):
                reason = code or "gate fault"
                best = verdict
                break

            # Kill is priced per rung, not per verdict: a $5 decline still cost
            # a live authorization attempt.
            if is_terminal(verdict):
                debit = cost
                total += debit
                rung.debited = debit
            if best is None or self._rank(verdict) > self._rank(best):
                best = verdict

        if total > 0:
            check = await repo.create_check(
                session,
                user_id=user.id,
                gate_id=gate.id,
                pan_masked=card.masked,
                pan_hash=card.pan_hash,
                verdict=(best or Verdict.DEAD).value,
                code=rungs[-1].code if rungs else "",
                decline_code=rungs[-1].decline_code if rungs else "",
                avs="",
                cvc="",
                three_ds="",
                latency_ms=sum(rung.latency_ms for rung in rungs),
                provider_ref=rungs[-1].provider_ref if rungs else "",
                amount_cents=rungs[-1].amount_cents if rungs else 0,
                debited=Decimal("0"),
            )
            balance = await ledger.debit(
                session,
                user.id,
                -total,
                "kill",
                f"chk:{check.id}",
                check_id=check.id,
            )
            check.debited = total
            if best is not None and not is_terminal(best):
                balance = await ledger.refund(
                    session, user.id, total, "refund:kill_fault", check.id
                )
                check.refunded = True
                check.debited = Decimal("0")
                total = Decimal("0")

        return KillOutcome(
            verdict=best or Verdict.GATE_FAULT,
            gate_id=gate.id,
            gate_label=gate.label,
            reached_cents=rungs[-1].amount_cents if rungs else 0,
            debited=total,
            balance=balance,
            pan_masked=card.masked,
            reason=reason,
            rungs=rungs,
        )

    @staticmethod
    def _rank(verdict: Verdict) -> int:
        return {
            Verdict.DEAD: 0,
            Verdict.LIVE_3DS: 1,
            Verdict.LIVE: 2,
            Verdict.APPROVED: 3,
        }.get(verdict, 0)

    async def _call(
        self,
        adapter: Adapter,
        key: Key,
        card: Card,
        amount: int,
        proxy_url: str | None,
    ) -> tuple[RawResult, int]:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=self.executor.settings.check_timeout_s,
                http2=False,
            ) as client:
                raw = await adapter.auth(client, key, card, amount, True)
        except httpx.HTTPError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return (
                RawResult(status=NO_BYTES, body={"error": {"code": "no_bytes"}}, raw_text=str(exc)),
                elapsed,
            )
        return raw, int((time.perf_counter() - started) * 1000)
