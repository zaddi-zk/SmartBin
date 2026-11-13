"""BIN resolver (section 17.5).

A lookup is a routing decision: it resolves fields from at least two sources,
records every disagreement, and stores the result with its provenance. The
record is the product; the renderer only formats it.

``bin_hits`` is written by the executor, never here. A lookup does not pretend
a card was checked.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bin import confidence, providers
from app.bin.model import BinRecord, FieldSource, HitStat, ThreeDSStat
from app.config import Settings
from app.db.models import (
    Bin3dsPassiveRow,
    Bin3dsRow,
    BinFieldReportRow,
    BinHitsRow,
    BinRecordRow,
    BinSourceRow,
)

_DIGITS = re.compile(r"\D+")
_BIN_INPUT = re.compile(r"^[\d\s\-]+$")


class BinUnavailable(RuntimeError):
    """No source returned a usable payload; nothing is cached."""


def canonical_bin(raw: str) -> str:
    """Return the 8-digit key for a BIN or a full PAN.

    A BIN lookup accepts 6-8 digits. A full PAN (13-19 digits) is truncated to
    its first 8 digits, because the executor passes ``card.number`` here. Any
    other input is rejected rather than silently stripped into a different BIN.
    """
    text = str(raw or "").strip()
    if not text or not _BIN_INPUT.match(text):
        raise ValueError("bin must be 6–8 digits")
    digits = _DIGITS.sub("", text)
    if 6 <= len(digits) <= 8:
        return digits
    if 13 <= len(digits) <= 19:
        return digits[:8]
    raise ValueError("bin must be 6–8 digits or a full PAN")


def _as_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in ("1", "true", "yes", "y")


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _trust_rank(name: str) -> int:
    return providers.TRUST.index(name) if name in providers.TRUST else len(providers.TRUST)


def _majority(values: list[tuple[str, str]]) -> str:
    """2-of-3 wins; otherwise the first value by the explicit trust order."""
    counts = Counter(value for value, _ in values)
    top, count = counts.most_common(1)[0]
    if count >= 2:
        return top
    return min(values, key=lambda item: _trust_rank(item[1]))[0]


def merge(results: list[tuple[str, dict[str, str]]]) -> tuple[dict[str, str | None], int]:
    """Merge field by field. Returns the merged values and a conflict count."""
    merged: dict[str, str | None] = {}
    conflicts = 0
    for field in providers.FIELDS:
        values: list[tuple[str, str]] = []
        for name, data in results:
            value = (data.get(field) or "").strip()
            if value:
                values.append((value, name))
        if not values:
            merged[field] = None
        elif len({value for value, _ in values}) == 1:
            merged[field] = values[0][0]
        else:
            conflicts += 1
            merged[field] = _majority(values)
    return merged, conflicts


def _field_confidence(
    field: str,
    results: list[tuple[str, dict[str, str]]],
    merged: str | None,
    record_confidence: int,
) -> int:
    """Agreement among reporting sources, capped by the record's confidence.

    A single source cannot make a field more certain than the record itself,
    which is why a one-source record shows the same low percentage on every
    field instead of a misleading 100%.
    """
    reporting = [data.get(field, "").strip() for _, data in results if data.get(field)]
    if not reporting:
        return 0
    agree = sum(1 for value in reporting if value == merged)
    return min(round(100 * agree / len(reporting)), record_confidence)


def _flags(merged: dict[str, str | None], sources_n: int, conflicts: int) -> list[str]:
    flags: list[str] = []
    if sources_n < 2:
        flags.append("single source")
    if not merged.get("type"):
        flags.append("type unresolved")
    if conflicts:
        flags.append(f"{conflicts} conflict" + ("s" if conflicts > 1 else ""))
    return flags


class BinResolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def resolve(
        self, session: AsyncSession, raw: str, *, force: bool = False
    ) -> BinRecord:
        key = canonical_bin(raw)
        cached = await self.load(session, key)
        if cached is not None and cached.resolved and not force and not cached.stale:
            return cached

        async with httpx.AsyncClient(timeout=self.settings.bin_timeout_s, http2=False) as client:
            fetched = await asyncio.gather(
                *(provider.fetch(client, key[:6]) for provider in providers.PROVIDERS),
                return_exceptions=True,
            )

        results: list[tuple[str, dict[str, str]]] = []
        for provider, payload in zip(providers.PROVIDERS, fetched, strict=True):
            if isinstance(payload, dict):
                results.append((provider.name, payload))

        if not results:
            raise BinUnavailable("no BIN source answered")

        merged, conflicts = merge(results)
        # Parent-unknown cascade: without TYPE the three class children are
        # unknowable. Asserting them would be a lie, so they become None.
        if not merged.get("type"):
            for child in ("prepaid", "commercial", "debit"):
                merged[child] = None

        sources_n = len(results)
        flags = _flags(merged, sources_n, conflicts)
        now = datetime.now(UTC)
        row = await session.get(BinRecordRow, key)
        if row is None:
            row = BinRecordRow(bin=key)
            session.add(row)

        row.parent6 = key[:6]
        row.brand = merged.get("brand")
        row.type = merged.get("type")
        row.tier = merged.get("tier")
        row.prepaid = _as_bool(merged.get("prepaid"))
        row.commercial = _as_bool(merged.get("commercial"))
        row.debit = _as_bool(merged.get("debit"))
        row.bank_short = merged.get("bank_short")
        row.bank_legal = merged.get("bank_legal")
        row.bank_phone = merged.get("bank_phone")
        row.bank_site = merged.get("bank_site")
        row.country = merged.get("country")
        row.iso2 = (merged.get("iso2") or "").upper()[:2] or None
        row.currency = (merged.get("currency") or "").upper()[:8] or None
        row.pan_len = _as_int(merged.get("pan_len"))
        row.range_lo = merged.get("range_lo")
        row.range_hi = merged.get("range_hi")
        row.conflicts = conflicts
        row.flags = flags
        row.sources_n = sources_n
        row.fetched_at = now
        row.ttl_days = max(1, self.settings.bin_cache_days)
        row.confidence = confidence.confidence(sources_n, 0, 0.0, conflicts)

        await session.execute(delete(BinSourceRow).where(BinSourceRow.bin == key))
        for name, data in results:
            for field in providers.FIELDS:
                value = (data.get(field) or "").strip()
                if not value:
                    continue
                session.add(
                    BinSourceRow(
                        bin=key,
                        source=name,
                        field=field,
                        value=value,
                        confidence=_field_confidence(
                            field, results, merged.get(field), row.confidence
                        ),
                        at=now,
                    )
                )
        await session.flush()

        record = await self.load(session, key)
        if record is None:  # pragma: no cover - flush guarantees the row
            raise BinUnavailable("record vanished after write")
        record.confidence = confidence.confidence(
            record.sources_n, record.sample, record.age_h, record.conflicts
        )
        return record

    async def load(self, session: AsyncSession, raw: str) -> BinRecord | None:
        key = canonical_bin(raw)
        row = await session.get(BinRecordRow, key)
        if row is None:
            return None
        passive_row = await session.get(Bin3dsPassiveRow, key)
        probe_row = await session.get(Bin3dsRow, key)
        hits_row = await session.get(BinHitsRow, key)
        source_rows = (
            await session.scalars(
                select(BinSourceRow)
                .where(BinSourceRow.bin == key)
                .order_by(BinSourceRow.field, BinSourceRow.source)
            )
        ).all()
        record = BinRecord(
            bin=row.bin,
            parent6=row.parent6,
            brand=row.brand,
            type=row.type,
            tier=row.tier,
            prepaid=row.prepaid,
            commercial=row.commercial,
            debit=row.debit,
            bank_short=row.bank_short,
            bank_legal=row.bank_legal,
            bank_phone=row.bank_phone,
            bank_site=row.bank_site,
            country=row.country,
            iso2=row.iso2,
            currency=row.currency,
            pan_len=row.pan_len,
            range_lo=row.range_lo,
            range_hi=row.range_hi,
            confidence=row.confidence,
            conflicts=row.conflicts,
            flags=list(row.flags or []),
            sources_n=row.sources_n,
            fetched_at=row.fetched_at,
            ttl_days=row.ttl_days,
            sources=[
                FieldSource(
                    source=item.source,
                    field=item.field,
                    value=item.value,
                    confidence=item.confidence,
                    at=item.at,
                )
                for item in source_rows
            ],
        )
        if passive_row is not None or probe_row is not None:
            record.r3ds = ThreeDSStat(
                reads=passive_row.reads if passive_row is not None else 0,
                challenges=passive_row.challenges if passive_row is not None else 0,
                last_at=passive_row.last_at if passive_row is not None else None,
                last_state=passive_row.last_state if passive_row is not None else "",
                probe_reads=probe_row.probe_reads if probe_row is not None else 0,
                probe_challenges=probe_row.probe_challenges if probe_row is not None else 0,
                probe_last_at=probe_row.probe_last_at if probe_row is not None else None,
                probe_last_state=probe_row.probe_last_state if probe_row is not None else "",
                source=probe_row.source if probe_row is not None else "",
            )
        if hits_row is not None:
            record.hits = HitStat(
                attempts=hits_row.attempts,
                approved=hits_row.approved,
                last_hit_at=hits_row.last_hit_at,
                users=hits_row.users,
            )
        record.confidence = confidence.confidence(
            record.sources_n, record.sample, record.age_h, record.conflicts
        )
        return record

    async def record_3ds(self, session: AsyncSession, raw: str, *, challenged: bool) -> None:
        """Passive, authoritative read from a real card dispatch."""
        key = canonical_bin(raw)
        row = await session.get(Bin3dsPassiveRow, key)
        if row is None:
            row = Bin3dsPassiveRow(bin=key, reads=0, challenges=0, last_state="")
            session.add(row)
        row.reads += 1
        if challenged:
            row.challenges += 1
        row.last_at = datetime.now(UTC)
        row.last_state = "YES" if challenged else "NO"
        await session.flush()

    async def record_probe_3ds(
        self, session: AsyncSession, raw: str, *, challenged: bool
    ) -> None:
        """Advisory probe read: a funded canary, not this BIN."""
        key = canonical_bin(raw)
        row = await session.get(Bin3dsRow, key)
        if row is None:
            row = Bin3dsRow(
                bin=key,
                probe_reads=0,
                probe_challenges=0,
                probe_last_state="",
                source="probe",
            )
            session.add(row)
        row.probe_reads += 1
        if challenged:
            row.probe_challenges += 1
        row.probe_last_at = datetime.now(UTC)
        row.probe_last_state = "YES" if challenged else "NO"
        row.source = "probe"
        await session.flush()

    async def report_field(
        self,
        session: AsyncSession,
        raw: str,
        *,
        field: str,
        claimed: str,
        user_id: int,
    ) -> None:
        key = canonical_bin(raw)
        session.add(
            BinFieldReportRow(
                bin=key,
                field=field[:32],
                claimed=claimed[:500],
                user_id=user_id,
                at=datetime.now(UTC),
            )
        )
        await session.flush()


def as_payload(record: BinRecord) -> dict[str, Any]:
    """Small helper for logs/tests; never used to render."""
    return {
        "bin": record.bin,
        "sources_n": record.sources_n,
        "conflicts": record.conflicts,
        "confidence": record.confidence,
        "flags": record.flags,
    }
