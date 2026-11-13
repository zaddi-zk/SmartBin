"""Confidence arithmetic and bands (section 17.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.bin import confidence
from app.bin.model import ThreeDSStat
from app.tg import render

from tests.bin.helpers import gate_view, record


def test_one_source_is_low() -> None:
    value = confidence.confidence(sources_n=1, sample=0, age_h=0, conflicts=0)
    assert value < 55
    assert confidence.band(value) == ("🔴", "low")


def test_two_agreeing_sources_reach_medium() -> None:
    value = confidence.confidence(sources_n=2, sample=0, age_h=48, conflicts=0)
    assert value == 60
    assert confidence.band(value) == ("🟡", "medium")


def test_two_sources_sample_zero_reads_zero_is_60_yellow_with_banner() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=2, fetched_at=now - timedelta(days=2), reads=0)
    value, chip, word = confidence.score(rec, now)
    assert value == 60
    assert chip == "🟡"
    assert word == "medium"
    text = render.render_bin(rec, [gate_view()], now)
    assert "ℹ️  advisory" in text
    assert "3DS unmeasured" in text


def test_three_sources_sample_ten_fresh_reads_zero_caps_yellow() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=3, attempts=10, fetched_at=now, reads=0)
    value, chip, _word = confidence.score(rec, now)
    assert value == confidence.UNMEASURED_CAP
    assert chip == "🟡"

    # The same row with five passive reads unlocks 🟢 and lifts the cap.
    rec.r3ds = ThreeDSStat(
        reads=5, challenges=2, last_at=now - timedelta(hours=3), last_state="YES"
    )
    value, chip, word = confidence.score(rec, now)
    assert value == 100
    assert chip == "🟢"
    assert word == "high"


def test_score_never_crosses_green_band_while_unmeasured() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=3, attempts=100, fetched_at=now, reads=0)
    value, chip, word = confidence.score(rec, now)
    assert value == confidence.UNMEASURED_CAP == 84
    assert chip == "🟡"
    assert word == "medium"

    rec.r3ds = ThreeDSStat(
        reads=confidence.GREEN_3DS_READS,
        challenges=1,
        last_at=now - timedelta(hours=1),
        last_state="YES",
    )
    value, chip, _word = confidence.score(rec, now)
    assert value == 100
    assert chip == "🟢"


def test_warm_oracle_lands_85_to_90_green() -> None:
    now = datetime.now(UTC)
    rec = record(
        sources_n=2,
        attempts=12,
        reads=6,
        challenges=3,
        last_state="YES",
        fetched_at=now - timedelta(hours=3),
    )
    value, chip, word = confidence.score(rec, now)
    assert 85 <= value <= 90
    assert chip == "🟢"
    assert word == "high"


def test_three_sources_add_cross_check_bonus() -> None:
    two = confidence.confidence(2, 0, 0, 0, reads=3)
    three = confidence.confidence(3, 0, 0, 0, reads=3)
    assert three == two + 15


def test_conflict_costs_twenty_each() -> None:
    clean = confidence.confidence(2, 5, 0, 0, reads=3)
    one = confidence.confidence(2, 5, 0, 1, reads=3)
    two = confidence.confidence(2, 5, 0, 2, reads=3)
    assert clean - one == 20
    assert clean - two == 40


def test_sample_and_freshness_bonuses() -> None:
    cold = confidence.confidence(1, 0, 48, 0, reads=3)
    fresh = confidence.confidence(1, 0, 1, 0, reads=3)
    read = confidence.confidence(1, 10, 1, 0, reads=3)
    assert fresh == cold + 5
    # Passing 3 samples lifts the "sample < 3" penalty; the 10th sample adds
    # the earned-reads bonus on top of that.
    assert confidence.confidence(1, 3, 1, 0, reads=3) == fresh + 10
    assert read == confidence.confidence(1, 3, 1, 0, reads=3) + 10


def test_score_is_clamped() -> None:
    assert confidence.confidence(0, 0, 10_000, 99, reads=3) == 0
    assert confidence.confidence(3, 1_000, 0, 0, reads=3) == 100


def test_band_floors() -> None:
    assert confidence.band(85) == ("🟢", "high")
    assert confidence.band(84) == ("🟡", "medium")
    assert confidence.band(55) == ("🟡", "medium")
    assert confidence.band(54) == ("🔴", "low")


def test_breakdown_sums_to_score() -> None:
    rec = record(sources_n=1, fetched_at=datetime.now(UTC) - timedelta(days=2))
    total = sum(delta for _, delta in confidence.breakdown(rec))
    assert total == confidence.score(rec)[0]


def test_projections_show_the_next_step() -> None:
    rec = record(sources_n=1, attempts=1)
    with_source, with_sample = confidence.projections(rec)
    assert with_source > confidence.score(rec)[0]
    assert with_sample >= confidence.score(rec)[0]
