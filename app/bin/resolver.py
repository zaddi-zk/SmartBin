"""BIN resolver (section 17): two sources, field-level agreement, 7-day cache."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.bin import providers
from app.config import Settings
from app.db.models import BinCacheRow

UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class BinInfo:
    bin: str
    brand: str = UNKNOWN
    type: str = UNKNOWN
    bank: str = UNKNOWN
    country: str = UNKNOWN
    iso2: str = ""
    three_ds: str = UNKNOWN
    prepaid: bool = False
    commercial: bool = False
    confidence: int = 0
    sources: int = 0
    disagreements: int = 0
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def below_threshold(self) -> bool:
        return self.confidence < 55

    @property
    def best_label(self) -> str:
        return f"{self.brand} · {self.type} · {self.bank}"


def merge(results: list[dict[str, str] | None]) -> tuple[dict[str, str], int, int, int]:
    present = [result for result in results if result]
    merged: dict[str, str] = {}
    disagreements = 0
    for key in providers.FIELDS:
        values = {result.get(key, "").strip() for result in present if result.get(key)}
        values.discard("")
        if len(values) == 1:
            merged[key] = values.pop()
        else:
            merged[key] = UNKNOWN
            if len(values) > 1:
                disagreements += 1
    confidence = max(0, 100 - 15 * disagreements - 20 * (1 if len(present) < 2 else 0))
    return merged, len(present), disagreements, confidence


class BinResolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def resolve(self, session: AsyncSession, pan: str) -> BinInfo:
        bin8 = pan[:8]
        bin6 = pan[:6]
        cached = await session.get(BinCacheRow, bin8)
        if cached is not None and self._fresh(cached.fetched_at):
            return self._from_row(bin8, cached)

        async with httpx.AsyncClient(timeout=self.settings.bin_timeout_s, http2=False) as client:
            results = await asyncio.gather(
                providers.binlist(client, bin6),
                providers.antipublic(client, bin6),
                return_exceptions=False,
            )

        merged, sources, disagreements, confidence = merge(list(results))
        info = BinInfo(
            bin=bin8,
            brand=merged.get("brand", UNKNOWN),
            type=merged.get("type", UNKNOWN),
            bank=merged.get("bank", UNKNOWN),
            country=merged.get("country", UNKNOWN),
            iso2=merged.get("iso2", ""),
            confidence=confidence,
            sources=sources,
            disagreements=disagreements,
            fields=merged,
        )
        payload: dict[str, Any] = {
            "brand": info.brand,
            "type": info.type,
            "bank": info.bank,
            "country": info.country,
            "iso2": info.iso2,
            "three_ds": info.three_ds,
            "prepaid": info.prepaid,
            "commercial": info.commercial,
            "fields": merged,
        }
        if cached is None:
            session.add(
                BinCacheRow(
                    bin=bin8,
                    json=payload,
                    sources=sources,
                    confidence=confidence,
                    fetched_at=datetime.now(UTC),
                )
            )
        else:
            cached.json = payload
            cached.sources = sources
            cached.confidence = confidence
            cached.fetched_at = datetime.now(UTC)
        await session.flush()
        return info

    @staticmethod
    def _fresh(fetched_at: datetime) -> bool:
        return datetime.now(UTC) - fetched_at < timedelta(days=7)

    @staticmethod
    def _from_row(bin8: str, row: BinCacheRow) -> BinInfo:
        payload = row.json if isinstance(row.json, dict) else {}
        fields = payload.get("fields")
        return BinInfo(
            bin=bin8,
            brand=str(payload.get("brand", UNKNOWN)),
            type=str(payload.get("type", UNKNOWN)),
            bank=str(payload.get("bank", UNKNOWN)),
            country=str(payload.get("country", UNKNOWN)),
            iso2=str(payload.get("iso2", "")),
            three_ds=str(payload.get("three_ds", UNKNOWN)),
            prepaid=bool(payload.get("prepaid", False)),
            commercial=bool(payload.get("commercial", False)),
            confidence=row.confidence,
            sources=row.sources,
            fields=dict(fields) if isinstance(fields, dict) else {},
        )
