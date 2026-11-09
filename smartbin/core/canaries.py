from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from config import Config
from core.gate import GateManager

log = logging.getLogger("smartbin.canary")

STALE_SECONDS = 30 * 60  # was 20 min - more forgiving for live checks
DEMOTE_BREAKER_MINUTES = 10  # was 15 min - faster recovery


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, index))]


def _load_log(raw: str | None) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
        return [e for e in data if isinstance(e, dict)][-30:]
    except ValueError:
        return []


def _windows(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Last windows of exactly 3 canary results."""
    out: list[list[dict[str, Any]]] = []
    for end in range(len(entries), 2, -3):
        window = entries[end - 3:end]
        if len(window) == 3:
            out.append(window)
    out.reverse()
    return out


class CanaryDaemon:
    """3 cards per gate. 0/3 kills. <=2/3 twice demotes. Rejoin needs 2 clean windows."""

    def __init__(
        self,
        db: Any,
        config: Config,
        gates: GateManager,
        idle_seconds: int | None = None,
    ):
        self.db = db
        self.config = config
        self.gates = gates
        self.idle_seconds = idle_seconds or config.canary_idle_seconds
        self.checks_per_window = config.canary_checks_per_window
        self._task: asyncio.Task[Any] | None = None
        self._stop = asyncio.Event()
        self._uses = 0
        self._rotated_at = time.monotonic()

    def start(self) -> asyncio.Task[Any]:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="canary-daemon")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def run(self) -> None:
        log.info("event=canary_daemon_start idle_seconds=%s", self.idle_seconds)
        await self.fire_all()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.idle_seconds)
                break
            except asyncio.TimeoutError:
                await self.fire_all()

    async def fire_all(self) -> list[dict[str, Any]]:
        summaries = []
        for gate in await self.db.list_gates():
            summaries.append(await self.fire_window(str(gate["name"])))
        return summaries

    async def maybe_fire_on_check(self, gate_name: str = "stripe") -> bool:
        gate = await self.db.get_gate(gate_name)
        if gate is None:
            return False
        count = int(gate["window_checks"]) + 1
        if count < self.checks_per_window:
            await self.db.update_gate(gate_name, window_checks=count)
            return False
        await self.db.update_gate(gate_name, window_checks=0)
        asyncio.create_task(self.fire_window(gate_name), name=f"canary-{gate_name}")
        return True

    def _pass(self, kind: str, result: Mapping[str, Any]) -> int:
        if kind == "nonvbv":
            return 1 if result.get("approved") else 0
        if kind == "vbv":
            return 1 if result.get("acs_host") else 0
        if kind == "dead":
            declined = bool(
                result.get("decline_code")
                or result.get("raw_code")
                or result.get("timeout")
                or result.get("error")
            )
            return 1 if declined and not result.get("approved") else 0
        return 0

    async def fire_window(self, gate_name: str = "stripe") -> dict[str, Any]:
        gate = await self.db.get_gate(gate_name)
        if gate is None:
            return {"gate": gate_name, "skipped": True}

        results: list[dict[str, Any]] = []
        for kind, card in self.config.canary_cards.items():
            result = await self.gates.test_canary(kind, card)
            results.append(
                {
                    "ts": _iso(_now()),
                    "card": kind,
                    "pass": self._pass(kind, result),
                    "latency_ms": int(result.get("latency_ms") or 0),
                }
            )
            self._uses += 1

        entries = (_load_log(gate["canary_log"]) + results)[-30:]
        now = _now()
        enabled = int(gate["enabled"])
        breaker_until = str(gate["breaker_until"] or "")
        action = "pass"
        windows = _windows(entries)
        last = windows[-1] if windows else []
        last_pass = sum(int(e.get("pass", 0)) for e in last) if last else 0

        if last_pass == 0:
            enabled = 0
            breaker_until = _iso(now + timedelta(minutes=DEMOTE_BREAKER_MINUTES))
            action = "kill"
        elif len(windows) >= 2 and all(
            sum(int(e.get("pass", 0)) for e in w) <= 2 for w in windows[-2:]
        ):
            enabled = 0
            breaker_until = _iso(now + timedelta(minutes=DEMOTE_BREAKER_MINUTES))
            action = "demote"
        else:
            if not enabled and len(windows) >= 2 and all(
                sum(int(e.get("pass", 0)) for e in w) == 3 for w in windows[-2:]
            ):
                enabled = 1
                breaker_until = ""
                action = "rejoin"
            elif enabled:
                breaker_until = ""

        latencies = [float(e.get("latency_ms", 0)) for e in entries]
        p50 = percentile(latencies, 50)
        p95 = percentile(latencies, 95)
        if len(latencies) >= 30 and (p50 > 3000 or p95 > 8000):
            enabled = 0
            breaker_until = _iso(now + timedelta(minutes=DEMOTE_BREAKER_MINUTES))
            action = "latency-demote"

        self._rotate_if_needed()
        await self.db.update_gate(
            gate_name,
            enabled=enabled,
            breaker_until=breaker_until,
            canary_log=json.dumps(entries, separators=(",", ":")),
            last_canary_utc=_iso(now),
        )
        summary = {
            "gate": gate_name,
            "window_pass": last_pass,
            "action": action,
            "enabled": enabled,
            "p50": p50,
            "p95": p95,
        }
        log.info(
            "event=canary_window gate=%s pass=%s/3 action=%s p50=%s p95=%s",
            gate_name,
            last_pass,
            action,
            p50,
            p95,
        )
        return summary

    def _rotate_if_needed(self) -> None:
        if self._uses >= 300 or (time.monotonic() - self._rotated_at) >= 6 * 3600:
            self._uses = 0
            self._rotated_at = time.monotonic()
            log.info("event=canary_rotate uses=300_or_6h")

    def window_state(self, gate: Mapping[str, Any] | None) -> int:
        if gate is None or not int(gate["enabled"]):
            return 0
        entries = _load_log(gate["canary_log"])
        windows = _windows(entries)
        if not windows:
            return -1
        last = windows[-1]
        # LIVE MODE: accept 1/3 passes (test cards don't work in live, only dead card works)
        # TEST MODE: accept 2/3 passes
        passes = sum(int(e.get("pass", 0)) for e in last)
        min_passes = 1 if not self.config.stripe_test_mode else 2
        if passes < min_passes:
            return 0
        raw = str(gate["last_canary_utc"] or "")
        if not raw:
            return -1
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if (_now() - stamp).total_seconds() > STALE_SECONDS:
                return -1
        except ValueError:
            return -1
        return 1

    def is_clean(self, gate: Mapping[str, Any] | None) -> bool:
        return self.window_state(gate) == 1

    def stats(self, gate: Mapping[str, Any] | None) -> dict[str, Any]:
        if gate is None:
            return {}
        entries = _load_log(gate["canary_log"])
        latencies = [float(e.get("latency_ms", 0)) for e in entries]
        passes = sum(int(e.get("pass", 0)) for e in entries)
        sample = len(entries)
        windows = _windows(entries)
        last_window = windows[-1] if windows else []
        return {
            "gate": str(gate["name"]),
            "enabled": int(gate["enabled"]),
            "window_state": self.window_state(gate),
            "window_pass": sum(int(e.get("pass", 0)) for e in last_window),
            "window_total": len(last_window),
            "pass_rate": round(100.0 * passes / sample, 1) if sample else 0.0,
            "sample": sample,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "last_canary_utc": str(gate["last_canary_utc"] or ""),
            "window_checks": int(gate["window_checks"]),
        }
