from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from config import Config
from core.canaries import CanaryDaemon
from core.gate import GateManager
from core.routing import amount_for, choose_gate, route
from core.twofa import TwoFARead, evaluate
from core.verdicts import map_result
from models import BinRecord, Card, CheckOutcome, utc_date, utcnow_iso

log = logging.getLogger("smartbin.check")


class CheckRunner:
    """RECEIVED -> PARSE -> ROUTE -> GATE PICK -> CANARY -> DISPATCH -> VERDICT -> WRITE-BACK."""

    def __init__(
        self,
        db: Any,
        store: Any,
        gates: GateManager,
        canary: CanaryDaemon,
        config: Config,
    ):
        self.db = db
        self.store = store
        self.gates = gates
        self.canary = canary
        self.config = config

    async def run(
        self,
        user_id: int,
        card_raw: str,
        purpose: str = "check",
        gate_name: str = "stripe",
    ) -> CheckOutcome:
        created = utcnow_iso()
        try:
            card = Card.parse(card_raw)
        except ValueError as exc:
            outcome = CheckOutcome(
                verdict="INVALID",
                live=0,
                action="parse-fail",
                note=f"parse-fail: {exc}",
                gate=gate_name,
            )
            await self._write(user_id, "", gate_name, 1, outcome, created)
            return outcome

        record = await self.store.get(card.bin8)
        routing = route(record.to_dict(), card)
        gates = await self.db.list_gates()
        gate = choose_gate(gates, routing, purpose)
        if gate is None:
            outcome = CheckOutcome(
                verdict="INCONCLUSIVE",
                live=0,
                action="core-degraded",
                note="CORE degraded, BIN-only mode: no healthy gate",
                gate=gate_name,
            )
            await self._write(user_id, card.bin8, gate_name, 1, outcome, created)
            return outcome

        gate_name = str(gate["name"])
        canary_state = self.canary.window_state(gate)
        if canary_state != 1:
            outcome = CheckOutcome(
                verdict="INCONCLUSIVE",
                live=0,
                action="canary-window-failed",
                note="canary window not clean at dispatch; user does not inherit gate fault",
                canary_ok=canary_state,
                gate=gate_name,
            )
            await self._write(user_id, card.bin8, gate_name, 1, outcome, created)
            return outcome

        proxies = await self.db.check_proxies(routing["iso2"])
        if not proxies:
            outcome = CheckOutcome(
                verdict="INCONCLUSIVE",
                live=0,
                action="no-proxy",
                note="INCONCLUSIVE-NO-PROXY: CORE degraded, BIN-only mode",
                canary_ok=canary_state,
                gate=gate_name,
            )
            await self._write(user_id, card.bin8, gate_name, 1, outcome, created)
            return outcome

        amount = amount_for(routing, purpose, self.config.eea_enrollment_min_minor)
        proxy = proxies[0]
        outcome, result = await self._dispatch(
            user_id, card, record, routing, gate, proxy, amount, purpose, canary_state, attempt=1, created=created
        )

        if outcome.retry:
            outcome, result = await self._retry(
                user_id, card, record, routing, gates, proxies, amount, purpose, outcome, created
            )

        if outcome.verdict == "AMBIGUOUS" and outcome.action == "second-gate-required":
            outcome = await self._second_gate(
                user_id, card, record, routing, gates, proxies, amount, purpose, outcome, created
            )

        # Legacy verdict map: translate check runner verdicts into twofa_read
        # values that the legacy ``bins`` table understands.  Without this map the
        # passive 3DS branch looks for ``LIVE_3DS`` which legacy never emits, so
        # ``challenges`` never increments and the field stays ``UNMEASURED``.
        _3DS_VERDICT_MAP: dict[str, str] = {
            "3DS-CHALLENGE": "VBV",          # challenge counted
            "APPROVED":      "NON-VBV",      # no challenge
            "LIVE-RESTRICTED": "NON-VBV",  # no challenge
            "INCONCLUSIVE":  "",              # skip
            "AMBIGUOUS":     "",              # skip
        }

        if outcome.verdict not in ("INCONCLUSIVE", "AMBIGUOUS"):
            approved = outcome.verdict in (
                "APPROVED",
                "3DS-CHALLENGE",
                "LIVE-RESTRICTED",
            )
            await self.db.bump_bin_hits(card.bin8, approved=approved, user_id=user_id)
            # Map verdict to twofa_read for legacy bins twofa field
            read_kind = _3DS_VERDICT_MAP.get(outcome.verdict, "")
            if read_kind:
                await self.db.update_bin_fields(
                    card.bin8,
                    {"twofa": read_kind, "twofa_conf": 95.0, "twofa_reads": 1, "last_twofa_utc": utcnow_iso()},
                )

        await self.canary.maybe_fire_on_check(gate_name)
        await self.db.stats_bump("checks_total")
        await self.db.stats_bump(f"day:{utc_date()}:checks")
        return outcome

    async def _dispatch(
        self,
        user_id: int,
        card: Card,
        record: BinRecord,
        routing: Mapping[str, Any],
        gate: Mapping[str, Any],
        proxy: Mapping[str, Any],
        amount: int,
        purpose: str,
        canary_state: int,
        attempt: int,
        created: str,
    ) -> tuple[CheckOutcome, dict[str, Any]]:
        gate_name = str(gate["name"])
        result = await self.gates.dispatch(
            card,
            amount_minor=amount,
            proxy=str(proxy["url"]),
            purpose=purpose,
            gate_name=gate_name,
            canary_ok=canary_state,
            proxy_id=int(proxy["id"]),
        )
        outcome = map_result(result, canary_state)
        outcome.gate = gate_name
        outcome.amount_band = amount
        outcome.proxy_id = int(proxy["id"])
        outcome.note = (outcome.note + " | " + str(routing["note"])).strip(" |")
        await self._proxy_health(proxy, result)
        await self._gate_health(gate_name, result)
        await self._twofa_writeback(card, record, routing, gate_name, outcome, result, amount)
        await self._write(user_id, card.bin8, gate_name, attempt, outcome, created)
        return outcome, result

    async def _retry(
        self,
        user_id: int,
        card: Card,
        record: BinRecord,
        routing: Mapping[str, Any],
        gates: list[Mapping[str, Any]],
        proxies: list[Mapping[str, Any]],
        amount: int,
        purpose: str,
        first: CheckOutcome,
        created: str,
    ) -> tuple[CheckOutcome, dict[str, Any]]:
        gate = choose_gate(gates, routing, purpose)
        if gate is None:
            return first, {}
        gate_name = str(gate["name"])
        state = self.canary.window_state(gate)
        fresh = [p for p in proxies if int(p["id"]) != first.proxy_id]
        if state != 1:
            outcome = CheckOutcome(
                verdict="AMBIGUOUS" if first.raw_code == "05" else "INCONCLUSIVE",
                live=0,
                action="retry-window-failed",
                note="retry blocked: canary window not clean",
                canary_ok=state,
                gate=gate_name,
                amount_band=amount,
            )
            await self._write(user_id, card.bin8, gate_name, 2, outcome, created)
            return outcome, {}
        if not fresh:
            outcome = CheckOutcome(
                verdict="INCONCLUSIVE",
                live=0,
                action="no-fresh-proxy",
                note="retry blocked: no fresh proxy",
                canary_ok=state,
                gate=gate_name,
                amount_band=amount,
            )
            await self._write(user_id, card.bin8, gate_name, 2, outcome, created)
            return outcome, {}

        proxy = fresh[0]
        result = await self.gates.dispatch(
            card,
            amount_minor=amount,
            proxy=str(proxy["url"]),
            purpose=purpose,
            gate_name=gate_name,
            canary_ok=state,
            proxy_id=int(proxy["id"]),
        )
        outcome = map_result(result, state)
        outcome.gate = gate_name
        outcome.amount_band = amount
        outcome.proxy_id = int(proxy["id"])
        outcome.note = f"attempt=2 retry after {first.verdict} | {outcome.note}"
        if first.verdict == "DO-NOT-HONOR" and outcome.verdict == "DO-NOT-HONOR":
            outcome.verdict = "ISSUER-BLOCK"
            outcome.live = 0
            outcome.action = "final"
            outcome.retry = False
            outcome.note = "two clean-gate 05s -> ISSUER-BLOCK final"
        elif first.verdict == "INCONCLUSIVE" and outcome.verdict == "INCONCLUSIVE":
            outcome.note = "timeout retry failed -> INCONCLUSIVE"
        await self._proxy_health(proxy, result)
        await self._gate_health(gate_name, result)
        await self._twofa_writeback(card, record, routing, gate_name, outcome, result, amount)
        await self._write(user_id, card.bin8, gate_name, 2, outcome, created)
        return outcome, result

    async def _second_gate(
        self,
        user_id: int,
        card: Card,
        record: BinRecord,
        routing: Mapping[str, Any],
        gates: list[Mapping[str, Any]],
        proxies: list[Mapping[str, Any]],
        amount: int,
        purpose: str,
        first: CheckOutcome,
        created: str,
    ) -> CheckOutcome:
        usable: list[dict[str, Any]] = []
        for raw in gates:
            candidate = dict(raw)
            if (
                candidate.get("enabled")
                and str(candidate["name"]) != first.gate
                and self.canary.window_state(candidate) == 1
            ):
                usable.append(candidate)
        if not usable:
            first.note = (first.note + " | second gate unavailable").strip(" |")
            return first
        gate = usable[0]
        proxy = proxies[-1]
        result = await self.gates.dispatch(
            card,
            amount_minor=amount,
            proxy=str(proxy["url"]),
            purpose=purpose,
            gate_name=str(gate["name"]),
            canary_ok=1,
            proxy_id=int(proxy["id"]),
        )
        second = map_result(result, 1)
        second.gate = str(gate["name"])
        second.amount_band = amount
        second.proxy_id = int(proxy["id"])
        second.note = f"second gate after AMBIGUOUS | {second.note}"
        await self._proxy_health(proxy, result)
        await self._gate_health(str(gate["name"]), result)
        await self._twofa_writeback(card, record, routing, str(gate["name"]), second, result, amount)
        await self._write(user_id, card.bin8, str(gate["name"]), 2, second, created)
        return second

    async def _twofa_writeback(
        self,
        card: Card,
        record: BinRecord,
        routing: Mapping[str, Any],
        gate_name: str,
        outcome: CheckOutcome,
        result: Mapping[str, Any],
        amount: int,
    ) -> None:
        kind = ""
        acs_host = outcome.acs_host
        gate_kind = "stripe"
        if outcome.verdict == "3DS-CHALLENGE" and acs_host:
            kind = "challenge"
        elif outcome.verdict == "APPROVED":
            kind = "nonvbv"
        elif bool(result.get("timeout")):
            kind = "timeout"
        elif outcome.decline_code == "authentication_required" and not acs_host:
            kind = "captcha"
        elif "3ds" in str(result.get("error") or "").lower() or "authentication" in str(result.get("error") or "").lower():
            kind = "soft_decline"
        if not kind:
            return

        row = await self.db.get_bin(card.bin8)
        current = BinRecord.from_row(row) if row is not None else record
        read = TwoFARead(
            kind=kind,
            gate=gate_name,
            ts=utcnow_iso(),
            amount_minor=amount,
            canary_ok=outcome.canary_ok == 1,
            acs_host=acs_host,
            iso2=str(routing.get("iso2") or ""),
            gate_kind=gate_kind,
        )
        history = await self.db.recent_twofa_reads(card.bin8)
        update = evaluate(current.to_dict(), read, history, self.config.eea_enrollment_min_minor)
        outcome.twofa_read = str(update.get("check_read") or "")
        if update.get("note"):
            outcome.note = (outcome.note + " | " + str(update["note"])).strip(" |")
        if update.get("write"):
            await self.db.update_bin_fields(
                card.bin8,
                {
                    "twofa": update["twofa"],
                    "twofa_conf": update["twofa_conf"],
                    "twofa_reads": update["twofa_reads"],
                    "last_twofa_utc": update["last_twofa_utc"],
                    "amt_tested": update["amt_tested"],
                },
            )
        if update.get("gate_penalty"):
            gate = await self.db.get_gate(gate_name)
            if gate is not None:
                fails = int(gate["breaker_fails"]) + 1
                fields: dict[str, Any] = {"breaker_fails": fails}
                if fails >= 5:
                    fields["enabled"] = 0
                    fields["breaker_until"] = (
                        datetime.now(timezone.utc) + timedelta(minutes=10)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                await self.db.update_gate(gate_name, **fields)

    async def _proxy_health(self, proxy: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        ok = not (result.get("timeout") or result.get("error"))
        fields = {
            "last_latency_ms": int(result.get("latency_ms") or 0),
        }
        if ok:
            fields["ok_count"] = int(proxy["ok_count"]) + 1
        else:
            fields["fail_count"] = int(proxy["fail_count"]) + 1
        await self.db.update_proxy(int(proxy["id"]), **fields)

    async def _gate_health(self, gate_name: str, result: Mapping[str, Any]) -> None:
        gate = await self.db.get_gate(gate_name)
        if gate is None:
            return
        if result.get("timeout") or result.get("error"):
            fails = int(gate["breaker_fails"]) + 1
            fields: dict[str, Any] = {"breaker_fails": fails}
            if fails >= 5:
                fields["enabled"] = 0
                fields["breaker_until"] = (
                    datetime.now(timezone.utc) + timedelta(minutes=10)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            await self.db.update_gate(gate_name, **fields)
        else:
            await self.db.update_gate(gate_name, breaker_fails=0)

    async def _write(
        self,
        user_id: int,
        bin8: str,
        gate_name: str,
        attempt: int,
        outcome: CheckOutcome,
        created: str,
    ) -> None:
        await self.db.add_check(
            {
                "user_id": user_id,
                "bin": bin8,
                "gate": gate_name,
                "attempt": attempt,
                "verdict": outcome.verdict,
                "action": outcome.action,
                "live": outcome.live,
                "raw_code": outcome.raw_code,
                "decline_code": outcome.decline_code,
                "twofa_read": outcome.twofa_read,
                "amount_band": outcome.amount_band,
                "canary_ok": outcome.canary_ok,
                "acs_host": outcome.acs_host,
                "latency_ms": outcome.latency_ms,
                "proxy_id": outcome.proxy_id,
                "note": outcome.note,
                "created_utc": created,
            }
        )
