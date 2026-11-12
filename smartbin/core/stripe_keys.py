from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

# Stripe error codes that mean the key itself cannot be used. Key rotation is
# the only recovery: a different account/key may still be healthy.
KEY_REJECTED_CODES = frozenset(
    {
        "account_invalid",
        "api_key_expired",
        "invalid_api_key",
        "secret_key_required",
    }
)


class StripeKeyRejected(Exception):
    """The key is unusable (revoked, expired, wrong account)."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class StripeKeyRateLimited(Exception):
    """Stripe returned 429 for this key; try another key."""


@dataclass
class _KeyState:
    failures: int = 0
    cooling_until: float = 0.0


@dataclass
class StripeKeyRing:
    """Ordered pool of Stripe secret keys with cooldown-aware failover.

    Keys rotate round-robin so load is spread across accounts. A key rejected
    by Stripe is parked with exponential backoff; a rate-limited key is parked
    only briefly. When every key is cooling the request fails fast instead of
    hammering all accounts.
    """

    keys: tuple[str, ...]
    rejected_cooldown: float = 900.0
    rate_limit_cooldown: float = 30.0
    max_cooldown: float = 21600.0
    _state: dict[str, _KeyState] = field(default_factory=dict, init=False, repr=False)
    _cursor: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        clean: list[str] = []
        for key in self.keys:
            key = str(key or "").strip()
            if key and key not in clean:
                clean.append(key)
        self.keys = tuple(clean)
        self._state = {key: _KeyState() for key in self.keys}

    def __len__(self) -> int:
        return len(self.keys)

    def __bool__(self) -> bool:
        return bool(self.keys)

    def candidates(self, now: float | None = None) -> list[str]:
        """Ready keys first, starting at the round-robin cursor."""
        if not self.keys:
            return []
        now = time.monotonic() if now is None else now
        start = self._cursor % len(self.keys)
        self._cursor = (start + 1) % len(self.keys)
        ordered = self.keys[start:] + self.keys[:start]
        return [key for key in ordered if self._state[key].cooling_until <= now]

    def mark_healthy(self, key: str) -> None:
        state = self._state.get(key)
        if state is not None:
            state.failures = 0
            state.cooling_until = 0.0

    def mark_rejected(self, key: str, now: float | None = None) -> None:
        state = self._state.get(key)
        if state is None:
            return
        now = time.monotonic() if now is None else now
        state.failures += 1
        wait = min(
            self.max_cooldown,
            self.rejected_cooldown * (2 ** (state.failures - 1)),
        )
        state.cooling_until = now + wait

    def mark_rate_limited(self, key: str, now: float | None = None) -> None:
        state = self._state.get(key)
        if state is None:
            return
        now = time.monotonic() if now is None else now
        state.cooling_until = max(state.cooling_until, now + self.rate_limit_cooldown)

    def snapshot(self, now: float | None = None) -> list[dict[str, Any]]:
        """Health view for logs/admin. Never exposes the full key."""
        now = time.monotonic() if now is None else now
        rows = []
        for key in self.keys:
            state = self._state[key]
            rows.append(
                {
                    "key": fingerprint(key),
                    "failures": state.failures,
                    "cooldown": max(0, int(state.cooling_until - now)),
                    "ready": state.cooling_until <= now,
                }
            )
        return rows


def fingerprint(key: str) -> str:
    """Safe log label: prefix plus last four characters only."""
    clean = str(key or "").strip()
    if len(clean) <= 12:
        return clean[:4] + "…" if clean else "-"
    return f"{clean[:7]}…{clean[-4:]}"
