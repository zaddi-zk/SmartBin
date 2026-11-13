"""Resolver merge, cache, provenance and stats (sections 17.2, 17.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from app.bin import providers
from app.bin.resolver import BinResolver, BinUnavailable, canonical_bin, merge
from app.config import Settings
from app.db import repo
from app.db.models import Bin3dsPassiveRow, Bin3dsRow, BinFieldReportRow, BinRecordRow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

BIN = "44654205"
BIN6 = "446542"

BINLIST = {
    "brand": "visa",
    "type": "credit",
    "prepaid": False,
    "bank": {
        "name": "Wells Fargo Bank, N.A.",
        "phone": "1-800-869-3557",
        "url": "wellsfargo.com",
    },
    "country": {"name": "United States", "alpha2": "US", "currency": "USD"},
    "number": {"length": 16},
}

ANTIPUBLIC = {
    "data": {
        "brand": "visa",
        "type": "credit",
        "prepaid": False,
        "commercial": False,
        "debit": False,
        "bank": "Wells Fargo Bank, N.A.",
        "bank_phone": "1-800-869-3557",
        "bank_url": "wellsfargo.com",
        "country": "United States",
        "iso2": "US",
        "currency": "USD",
        "pan_len": "16",
    }
}


def settings() -> Settings:
    return Settings(
        _env_file=None,
        live_only=False,
        tg_token="",
        bin_timeout_s=2.0,
        bin_cache_days=7,
    )


def resolver() -> BinResolver:
    return BinResolver(settings())


def mock_binlist(payload: dict[str, object] | None = None, status: int = 200) -> None:
    respx.get(f"{providers.BINLIST_URL}/{BIN6}").mock(
        return_value=httpx.Response(status, json=payload or BINLIST)
    )


def mock_antipublic(payload: dict[str, object] | None = None, status: int = 200) -> None:
    respx.get(f"{providers.ANTIPUBLIC_URL}/{BIN6}").mock(
        return_value=httpx.Response(status, json=payload or ANTIPUBLIC)
    )


@respx.mock
async def test_one_source_still_renders_with_advisory(session: AsyncSession) -> None:
    mock_binlist()
    mock_antipublic(status=404)
    rec = await resolver().resolve(session, BIN)
    assert rec.sources_n == 1
    assert rec.conflicts == 0
    assert "single source" in rec.flags
    assert rec.confidence < 55
    assert rec.bin == BIN
    assert rec.parent6 == BIN6
    # With one source, no field can be more certain than the record itself.
    assert rec.sources
    assert all(source.confidence == rec.confidence for source in rec.sources)


@respx.mock
async def test_two_agreeing_sources_raise_confidence(session: AsyncSession) -> None:
    mock_binlist()
    mock_antipublic()
    rec = await resolver().resolve(session, BIN)
    assert rec.sources_n == 2
    assert rec.conflicts == 0
    assert "single source" not in rec.flags
    assert rec.confidence >= 55
    assert rec.brand == "visa"
    assert rec.type == "credit"
    assert rec.iso2 == "US"
    assert rec.pan_len == 16


@respx.mock
async def test_conflicting_field_is_counted_and_provenanced(session: AsyncSession) -> None:
    mock_binlist()
    split = {"data": {**ANTIPUBLIC["data"], "bank": "ADCB Islamic"}}
    mock_antipublic(payload=split)
    rec = await resolver().resolve(session, BIN)
    assert rec.conflicts == 1
    assert any("conflict" in flag for flag in rec.flags)
    bank_values = {source.value for source in rec.sources if source.field == "bank_short"}
    assert bank_values == {"Wells Fargo Bank, N.A.", "ADCB Islamic"}


@respx.mock
async def test_missing_type_cascades_children_to_none(session: AsyncSession) -> None:
    mock_binlist(payload={"brand": "visa", "country": {"name": "United States", "alpha2": "US"}})
    mock_antipublic(payload={"data": {"brand": "visa", "iso2": "US"}})
    rec = await resolver().resolve(session, BIN)
    assert rec.type is None
    assert rec.prepaid is None
    assert rec.commercial is None
    assert rec.debit is None
    assert "type unresolved" in rec.flags


@respx.mock
async def test_cache_avoids_a_second_fetch_until_forced(session: AsyncSession) -> None:
    route = respx.get(f"{providers.BINLIST_URL}/{BIN6}").mock(
        return_value=httpx.Response(200, json=BINLIST)
    )
    mock_antipublic()
    await resolver().resolve(session, BIN)
    await resolver().resolve(session, BIN)
    assert route.call_count == 1
    await resolver().resolve(session, BIN, force=True)
    assert route.call_count == 2


@respx.mock
async def test_stale_record_is_refetched(session: AsyncSession) -> None:
    route = respx.get(f"{providers.BINLIST_URL}/{BIN6}").mock(
        return_value=httpx.Response(200, json=BINLIST)
    )
    mock_antipublic()
    await resolver().resolve(session, BIN)
    row = await session.get(BinRecordRow, BIN)
    assert row is not None
    row.fetched_at = datetime.now(UTC) - timedelta(days=8)
    await session.flush()
    await resolver().resolve(session, BIN)
    assert route.call_count == 2


@respx.mock
async def test_zero_sources_is_unavailable_and_not_cached(session: AsyncSession) -> None:
    mock_binlist(status=404)
    mock_antipublic(status=500)
    with pytest.raises(BinUnavailable):
        await resolver().resolve(session, BIN)
    assert await resolver().load(session, BIN) is None


@respx.mock
async def test_hits_are_written_by_the_executor_side(session: AsyncSession) -> None:
    mock_binlist()
    mock_antipublic()
    await resolver().resolve(session, BIN)
    await repo.bump_bin_hits(session, BIN, approved=True, user_id=1)
    await repo.bump_bin_hits(session, BIN, approved=False, user_id=1)
    rec = await resolver().load(session, BIN)
    assert rec is not None
    assert rec.hits.attempts == 2
    assert rec.hits.approved == 1


async def test_3ds_reads_accumulate(session: AsyncSession) -> None:
    session.add(
        BinRecordRow(
            bin=BIN,
            parent6=BIN6,
            sources_n=2,
            conflicts=0,
            confidence=80,
            flags=[],
            fetched_at=datetime.now(UTC),
            ttl_days=7,
        )
    )
    await session.flush()
    bin_resolver = resolver()
    await bin_resolver.record_3ds(session, BIN, challenged=True)
    await bin_resolver.record_3ds(session, BIN, challenged=False)
    rec = await bin_resolver.load(session, BIN)
    assert rec is not None
    assert rec.r3ds.reads == 2
    assert rec.r3ds.challenges == 1
    assert rec.r3ds.last_state == "NO"
    assert rec.r3ds.probe_reads == 0


async def test_probe_3ds_is_a_separate_advisory_channel(session: AsyncSession) -> None:
    session.add(
        BinRecordRow(
            bin=BIN,
            parent6=BIN6,
            sources_n=2,
            conflicts=0,
            confidence=80,
            flags=[],
            fetched_at=datetime.now(UTC),
            ttl_days=7,
        )
    )
    await session.flush()
    bin_resolver = resolver()
    await bin_resolver.record_3ds(session, BIN, challenged=False)
    await bin_resolver.record_probe_3ds(session, BIN, challenged=True)
    await bin_resolver.record_probe_3ds(session, BIN, challenged=False)

    passive = await session.get(Bin3dsPassiveRow, BIN)
    probe = await session.get(Bin3dsRow, BIN)
    assert passive is not None and passive.reads == 1 and passive.challenges == 0
    assert probe is not None and probe.probe_reads == 2 and probe.probe_challenges == 1
    assert probe.source == "probe"
    assert probe.probe_last_state == "NO"

    rec = await bin_resolver.load(session, BIN)
    assert rec is not None
    assert rec.r3ds.reads == 1
    assert rec.r3ds.probe_reads == 2
    assert rec.r3ds.source == "probe"


def test_canonical_bin_accepts_6_to_8_digits() -> None:
    assert canonical_bin("446542") == "446542"
    assert canonical_bin("4465420") == "4465420"
    assert canonical_bin("44654205") == "44654205"


def test_canonical_bin_truncates_a_full_pan_to_eight() -> None:
    assert canonical_bin("4465420512345678") == "44654205"
    assert canonical_bin("4465 4205 1234 5678") == "44654205"


@pytest.mark.parametrize("raw", ["", "12345", "123456789", "123456789012", "abc", "4465x205"])
def test_canonical_bin_rejects_non_bin_input(raw: str) -> None:
    with pytest.raises(ValueError):
        canonical_bin(raw)


def test_trust_order_is_explicit_and_used_for_a_tie() -> None:
    assert providers.TRUST == ("binlist", "antipublic")
    assert providers.PROVIDER_NAMES == providers.TRUST
    merged, conflicts = merge(
        [
            ("antipublic", {"brand": "mastercard"}),
            ("binlist", {"brand": "visa"}),
        ]
    )
    assert conflicts == 1
    assert merged["brand"] == "visa"


async def test_field_report_is_stored(session: AsyncSession) -> None:
    await resolver().report_field(
        session, BIN, field="bank_short", claimed="Wells Fargo", user_id=7
    )
    rows = (await session.scalars(select(BinFieldReportRow))).all()
    assert len(rows) == 1
    assert rows[0].bin == BIN
    assert rows[0].field == "bank_short"
    assert rows[0].claimed == "Wells Fargo"
    assert rows[0].user_id == 7
