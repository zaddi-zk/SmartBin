"""Confidence (section 17.3).

The score is a function of evidence, never of a provider's own opinion:
agreement between sources, sample size, freshness and conflicts. 3DS is not
scored as a bonus; it is a hard ceiling, because a record with fewer than
three real 3DS reads is never allowed to cross into the green band.

``BASE = 45`` is the floor: two agreeing sources with no sample land at 60,
which is yellow, and the 17.3 deltas still apply on top. The green band starts
at 85, so an unmeasured record is capped at 84 and can never look green.
"""

from __future__ import annotations

from datetime import datetime

from app.bin.model import BinRecord, age_hours

BASE = 45

BANDS: tuple[tuple[int, str, str], ...] = (
    (85, "🟢", "high"),
    (55, "🟡", "medium"),
    (0, "🔴", "low"),
)

# Passive reads needed before the score may enter the green band.
GREEN_3DS_READS = 3
# Highest score an unmeasured (fewer than three passive reads) record can reach.
UNMEASURED_CAP = 84


def confidence(
    sources_n: int, sample: int, age_h: float, conflicts: int, reads: int = 0
) -> int:
    score = BASE
    score += 25 if sources_n >= 2 else 0
    score += 15 if sources_n >= 3 else 0
    score += 10 if sample >= 10 else 0
    score += 5 if age_h <= 24 else 0
    score -= 20 * conflicts
    score -= 15 if sources_n < 2 else 0
    score -= 10 if sample < 3 else 0
    if reads < GREEN_3DS_READS:
        score = min(score, UNMEASURED_CAP)
    return max(0, min(100, score))


def band(score: int) -> tuple[str, str]:
    for floor, chip, word in BANDS:
        if score >= floor:
            return chip, word
    return "🔴", "low"


def score(rec: BinRecord, now: datetime | None = None) -> tuple[int, str, str]:
    age = age_hours(rec.fetched_at, now)
    value = confidence(
        rec.sources_n, rec.sample, age, rec.conflicts, reads=rec.r3ds.reads
    )
    chip, word = band(value)
    return value, chip, word


def breakdown(rec: BinRecord, now: datetime | None = None) -> list[tuple[str, int]]:
    """The same arithmetic as :func:`confidence`, one line per term.

    Zero-valued terms are included so the evidence screen can say *why* a
    bonus is missing, not just what the total is.
    """
    age = age_hours(rec.fetched_at, now)
    terms = [
        ("base", BASE),
        ("sources ≥ 2 — agreement bonus", 25 if rec.sources_n >= 2 else 0),
        ("sources ≥ 3 — cross-check bonus", 15 if rec.sources_n >= 3 else 0),
        ("sample ≥ 10 — earned reads", 10 if rec.sample >= 10 else 0),
        ("fresh ≤ 24h", 5 if age <= 24 else 0),
        ("conflicts", -20 * rec.conflicts),
        ("single source", -15 if rec.sources_n < 2 else 0),
        ("sample < 3", -10 if rec.sample < 3 else 0),
    ]
    raw = sum(delta for _, delta in terms)
    capped = confidence(
        rec.sources_n, rec.sample, age, rec.conflicts, reads=rec.r3ds.reads
    )
    if capped != raw:
        terms.append(("3DS reads < 3 — cap", capped - raw))
    return terms


def projections(rec: BinRecord, now: datetime | None = None) -> tuple[int, int]:
    """What one more source and one more read would be worth."""
    age = age_hours(rec.fetched_at, now)
    with_source = confidence(
        rec.sources_n + 1, rec.sample, age, rec.conflicts, reads=rec.r3ds.reads
    )
    with_sample = confidence(
        rec.sources_n, rec.sample + 1, age, rec.conflicts, reads=rec.r3ds.reads
    )
    return with_source, with_sample
