"""BIN record domain model (section 17.2).

Plain dataclasses, no SQLAlchemy and no aiogram, so the renderer and the
confidence rules can be unit-tested without a database. ``None`` means "the
sources did not say"; it is never rendered as the word UNKNOWN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class ThreeDSStat:
    # Passive channel: real dispatches against this BIN. Authoritative.
    reads: int = 0
    challenges: int = 0
    last_at: datetime | None = None
    last_state: str = ""  # "YES", "NO" or "" when never measured

    # Probe channel: a funded canary of the same country. Advisory.
    probe_reads: int = 0
    probe_challenges: int = 0
    probe_last_at: datetime | None = None
    probe_last_state: str = ""
    source: str = ""

    @property
    def measured(self) -> bool:
        return self.reads > 0

    @property
    def probe_measured(self) -> bool:
        return self.probe_reads > 0


@dataclass(slots=True)
class HitStat:
    attempts: int = 0
    approved: int = 0
    last_hit_at: datetime | None = None
    users: int = 0


@dataclass(slots=True)
class FieldSource:
    source: str
    field: str
    value: str
    confidence: int = 0
    at: datetime | None = None


@dataclass(slots=True)
class BinRecord:
    bin: str
    parent6: str = ""
    brand: str | None = None
    type: str | None = None
    tier: str | None = None
    prepaid: bool | None = None
    commercial: bool | None = None
    debit: bool | None = None
    bank_short: str | None = None
    bank_legal: str | None = None
    bank_phone: str | None = None
    bank_site: str | None = None
    country: str | None = None
    iso2: str | None = None
    currency: str | None = None
    pan_len: int | None = None
    range_lo: str | None = None
    range_hi: str | None = None
    confidence: int = 0
    conflicts: int = 0
    flags: list[str] = field(default_factory=list)
    sources_n: int = 0
    fetched_at: datetime | None = None
    ttl_days: int = 7
    r3ds: ThreeDSStat = field(default_factory=ThreeDSStat)
    hits: HitStat = field(default_factory=HitStat)
    sources: list[FieldSource] = field(default_factory=list)

    @property
    def sample(self) -> int:
        """Checks the executor has dispatched against this BIN."""
        return self.hits.attempts

    @property
    def age_h(self) -> float:
        return age_hours(self.fetched_at)

    @property
    def stale(self) -> bool:
        return self.age_h > self.ttl_days * 24

    @property
    def resolved(self) -> bool:
        return self.fetched_at is not None


def age_hours(moment: datetime | None, now: datetime | None = None) -> float:
    if moment is None:
        return 0.0
    current = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (current - moment).total_seconds() / 3600.0)
