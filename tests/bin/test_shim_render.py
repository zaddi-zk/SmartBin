"""Acceptance for the section 17 BIN port, exercised through the shim.

``smartbin/bin_render.py`` is the renderer the live bot serves;
``smartbin/bin_shim.py`` maps a legacy ``bins`` row onto the section 17
``BinRecord``. These tests never render through ``app.tg.render``: they prove
the port, not the source tree it was copied from.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import bin_render
import bin_shim
import pytest
import ui

from tests.bin import test_render as app_render_tests

NOW = datetime(2026, 11, 13, 14, 0, tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def legacy_row(**overrides: Any) -> dict[str, Any]:
    sources = {
        "bank": {"binlist": "Wells Fargo Bank, National Association"},
        "commercial": {"binlist": "0"},
        "country": {"binlist": "United States"},
        "currency": {"binlist": "USD"},
        "iso2": {"binlist": "US"},
        "prepaid": {"binlist": "0"},
        "tier": {"binlist": "platinum"},
    }
    confs = {field: 42 for field in sources}
    row: dict[str, Any] = {
        "bin": "44654205",
        "brand": "VISA",
        "level": "PLATINUM",
        "bank": "Wells Fargo Bank, National Association",
        "phone": "1-800-869-3557",
        "site": "https://wellsfargo.com/",
        "country": "United States",
        "iso2": "US",
        "currency": "USD",
        "prepaid": 0,
        "commercial": 0,
        "card_length": 16,
        "luhn": 1,
        "avs": 1,
        "sources_json": json.dumps(sources),
        "field_conf_json": json.dumps(confs),
        "verified_utc": _stamp(NOW - timedelta(days=3)),
        "sample_size": 1,
        "twofa": "UNKNOWN",
        "twofa_conf": 0,
        "twofa_reads": 0,
        "last_twofa_utc": "",
    }
    row.update(overrides)
    return row


DEFAULT_GATES: tuple[dict[str, Any], ...] = (
    {"name": "stripe", "kind": "stripe", "enabled": 1},
)


def _gate_views(*rows: dict[str, Any]) -> list[bin_render.GateView]:
    return bin_shim.gates_from_rows(list(rows), NOW)


def render(
    row: dict[str, Any],
    *,
    challenges: int = 0,
    last_state: str = "",
    hits: dict[str, Any] | None = None,
    gate_rows: tuple[dict[str, Any], ...] = DEFAULT_GATES,
    now: datetime = NOW,
) -> str:
    rec = bin_shim.from_legacy(
        row,
        challenges=challenges,
        last_state=last_state,
        hits=hits,
        ttl_days=0,
        now=now,
    )
    conf, chip, word = bin_shim.confidence(rec, now)
    return bin_render.render_bin(rec, conf, chip, word, _gate_views(*gate_rows), now)


def test_no_unknown_for_3ds_anywhere() -> None:
    text = render(legacy_row())
    assert "UNKNOWN" not in text
    assert "🔐" in text
    assert "UNMEASURED" in text


def test_unmeasured_3ds_has_no_dead_end_button() -> None:
    text = render(legacy_row())
    assert "▶ measure" not in text
    labels = [
        button.text for row in ui.bin_kb("44654205").inline_keyboard for button in row
    ]
    assert not any("Measure 3DS" in label or "Re-probe" in label for label in labels)


def test_cold_and_warm_are_distinguishable_in_under_a_second() -> None:
    warm_row = legacy_row(
        twofa="VBV",
        twofa_conf=95,
        twofa_reads=6,
        last_twofa_utc=_stamp(NOW - timedelta(hours=3)),
    )
    hits = {
        "attempts": 12,
        "approved": 0,
        "users": 1,
        "last_hit_utc": _stamp(NOW - timedelta(minutes=53)),
    }
    start = time.perf_counter()
    cold = render(legacy_row())
    warm = render(warm_row, challenges=3, last_state="YES", hits=hits)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
    assert cold != warm
    assert "📶 20%  🔴" in cold
    assert "📶 40%  🔴" in warm
    assert "YES · 3/6 challenged · 50%" in warm
    assert "HITS           0 / 12" in warm


def test_second_column_is_aligned_on_all_three_identity_rows() -> None:
    lines = render(legacy_row()).splitlines()
    pairs = (
        ("💳", bin_render.ICONS["TIER"]),
        (bin_render.ICONS["TYPE"], bin_render.ICONS["COMMERCIAL"]),
        (bin_render.ICONS["PREPAID"], bin_render.ICONS["DEBIT"]),
    )
    indexes: list[int] = []
    for first, second in pairs:
        line = next(line for line in lines if first in line and second in line)
        indexes.append(line.index(second))
    assert len(set(indexes)) == 1, indexes


def test_identity_values_are_uppercase() -> None:
    text = render(legacy_row())
    assert "VISA" in text
    assert "PLATINUM" in text
    assert "visa" not in text
    assert "platinum" not in text


def test_exactly_one_copy_button() -> None:
    labels = [
        button.text for row in ui.bin_kb("44654205").inline_keyboard for button in row
    ]
    assert sum(1 for label in labels if label.startswith("📋")) == 1


def test_main_view_has_no_flat_42_and_no_field_rows() -> None:
    text = render(legacy_row())
    assert "42%" not in text
    assert "📊 FIELD" not in text


def test_type_null_forces_all_three_children_to_question_mark() -> None:
    text = render(legacy_row(prepaid=1, commercial=1, atm_debit=1))
    assert f"{bin_render.ICONS['COMMERCIAL']} COMMERCIAL  ?" in text
    assert f"{bin_render.ICONS['PREPAID']} {bin_render.pad('PREPAID')}?" in text
    assert f"{bin_render.ICONS['DEBIT']} DEBIT  ?" in text


def test_render_twice_is_byte_identical() -> None:
    assert render(legacy_row()) == render(legacy_row())


def test_single_source_is_advisory_red_and_honest() -> None:
    text = render(legacy_row())
    assert "ℹ️  advisory · only one source" in text
    assert "🔴" in text
    assert "📚 1 source" in text
    assert "permanent" in text


def test_gate_block_is_ranked_priced_and_enabled_only() -> None:
    rows = (
        {"name": "stripe", "kind": "stripe", "enabled": 1},
        {"name": "adyen", "kind": "adyen", "enabled": 0},
    )
    text = render(legacy_row(), gate_rows=rows)
    assert "Stripe 1cr (3DS)" in text
    assert "Adyen" not in text


def test_zero_enabled_gates_says_none_enabled() -> None:
    rows = ({"name": "stripe", "kind": "stripe", "enabled": 0},)
    text = render(legacy_row(), gate_rows=rows)
    assert f"🛡️ {bin_render.pad('BEST GATES')}none enabled" in text


def test_footer_reports_the_port_version() -> None:
    assert "SMARTBIN v7.3.0" in render(legacy_row())


def test_provenance_sub_screen_keeps_the_field_rows() -> None:
    rec = bin_shim.from_legacy(legacy_row(), ttl_days=0, now=NOW)
    text = bin_render.render_provenance(rec, NOW)
    assert "🏛 bank" in text
    assert "binlist" in text
    assert "Wells Fargo" in text


def test_evidence_screen_never_says_unknown() -> None:
    rec = bin_shim.from_legacy(legacy_row(), ttl_days=0, now=NOW)
    text = bin_render.render_evidence(rec, NOW)
    assert "UNKNOWN" not in text
    assert "3DS reads <b>0</b>" in text
    assert "unmeasured" in text


def test_shim_reads_passive_3ds_from_the_legacy_row() -> None:
    row = legacy_row(
        twofa="VBV",
        twofa_conf=95,
        twofa_reads=6,
        last_twofa_utc=_stamp(NOW - timedelta(hours=3)),
    )
    rec = bin_shim.from_legacy(row, challenges=3, last_state="YES", now=NOW)
    assert rec.r3ds.reads == 6
    assert rec.r3ds.challenges == 3
    assert rec.r3ds.last_state == "YES"


def test_shim_never_renders_more_challenges_than_reads() -> None:
    row = legacy_row(twofa="VBV", twofa_reads=2)
    rec = bin_shim.from_legacy(row, challenges=9, last_state="YES", now=NOW)
    assert rec.r3ds.challenges == 2


def test_warm_3ds_line_is_measured_not_unknown() -> None:
    row = legacy_row(
        twofa="VBV",
        twofa_conf=95,
        twofa_reads=6,
        last_twofa_utc=_stamp(NOW - timedelta(hours=3)),
    )
    text = render(row, challenges=3, last_state="YES")
    assert "🔐 3DS            YES · 3/6 challenged · 50% · 3h" in text


# The app render suite is the source of truth for renderer behaviour. Run its
# assertions again with ``render`` swapped for the ported implementation.
_REPLACED = {
    # The port renders bare "UNMEASURED" until measure_3ds() is ported. These
    # two app tests assert the dead-end button the port deliberately removed.
    "test_one_source_is_advisory_low_and_still_runnable",
    "test_both_channels_zero_is_unmeasured_with_measure_button",
    # The gates/share sub-screens were not part of the port.
    "test_icons_are_unique_across_every_bin_screen",
    "test_sub_screens_escape_every_interpolated_value",
}


class _ShimRender:
    """The ``app.tg.render`` surface, backed by the ported renderer."""

    def __getattr__(self, name: str) -> Any:
        return getattr(bin_render, name)

    @staticmethod
    def render_bin(record: Any, gates: Any, now: datetime | None = None) -> str:
        conf, chip, word = bin_shim.confidence(record, now)
        return bin_render.render_bin(record, conf, chip, word, gates, now)


_SHIM_RENDER = _ShimRender()

_APP_RENDER_TESTS = sorted(
    name
    for name in dir(app_render_tests)
    if name.startswith("test_") and name not in _REPLACED
)


@pytest.mark.parametrize("name", _APP_RENDER_TESTS)
def test_app_render_assertion_against_the_shim(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_render_tests, "render", _SHIM_RENDER)
    getattr(app_render_tests, name)()
