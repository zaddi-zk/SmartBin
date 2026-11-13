"""BIN renderer and keyboard acceptance (sections 17.6, 17.7, 17.10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.bin import confidence
from app.bin.model import FieldSource
from app.tg import keyboards as kb
from app.tg import render

from tests.bin.helpers import gate_view, record


def buttons(markup: kb.KB) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_one_source_is_advisory_low_and_still_runnable() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=1, fetched_at=now - timedelta(days=2))
    text = render.render_bin(rec, [gate_view()], now)
    assert "ℹ️  advisory · only one source" in text
    assert "3DS unmeasured" in text
    assert "🔴" in text
    assert "UNMEASURED  ▶ measure" in text
    assert "UNKNOWN" not in text
    assert any(text.startswith("▶️ Check on") for text in buttons(kb.bin_result(rec, [gate_view()])))


def test_high_confidence_needs_three_passive_reads_and_no_banner() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=3, attempts=10, reads=5, challenges=2, last_state="YES", fetched_at=now)
    text = render.render_bin(rec, [gate_view()], now)
    assert "advisory" not in text
    assert "🟢" in text
    assert any(text.startswith("▶️ Check on") for text in buttons(kb.bin_result(rec, [gate_view()])))


def test_conflict_costs_twenty_and_renders_a_row() -> None:
    clean = confidence.confidence(2, 0, 0, 0)
    conflicted = confidence.confidence(2, 0, 0, 1)
    assert clean - conflicted == 20

    now = datetime.now(UTC)
    rec = record(sources_n=2, conflicts=1, fetched_at=now)
    text = render.render_bin(rec, [gate_view()], now)
    assert "⚔️ 1 field · sources disagree" in text
    assert "sources disagree on 1 field" in text

    rec.sources = [
        FieldSource(source="binlist", field="bank_short", value="Abu Dhabi Commercial Bank"),
        FieldSource(source="antipublic", field="bank_short", value="ADCB Islamic"),
    ]
    provenance = render.render_provenance(rec, now)
    assert "Abu Dhabi Commercial Bank vs ADCB Islamic" in provenance


def test_type_unknown_forces_all_three_children() -> None:
    now = datetime.now(UTC)
    rec = record(
        sources_n=2,
        type_=None,
        prepaid=None,
        commercial=None,
        debit=None,
    )
    text = render.render_bin(rec, [gate_view()], now)
    assert f"{render.ICONS['TYPE']} {render.pad('TYPE')}?" in text
    assert f"{render.ICONS['COMMERCIAL']} COMMERCIAL  ?" in text
    assert f"{render.ICONS['PREPAID']} {render.pad('PREPAID')}?" in text
    assert f"{render.ICONS['DEBIT']} DEBIT  ?" in text


def test_type_unknown_wins_over_stale_children() -> None:
    now = datetime.now(UTC)
    rec = record(
        sources_n=2,
        type_=None,
        prepaid=True,
        commercial=True,
        debit=True,
    )
    text = render.render_bin(rec, [gate_view()], now)
    assert f"{render.ICONS['COMMERCIAL']} COMMERCIAL  ?" in text
    assert f"{render.ICONS['PREPAID']} {render.pad('PREPAID')}?" in text
    assert f"{render.ICONS['DEBIT']} DEBIT  ?" in text


def test_zero_reads_is_unmeasured_with_a_measure_button() -> None:
    now = datetime.now(UTC)
    rec = record(reads=0)
    text = render.render_bin(rec, [gate_view()], now)
    assert "UNMEASURED" in text
    assert "UNKNOWN" not in text
    assert "🔐 Measure 3DS · 1cr" in buttons(kb.bin_result(rec, [gate_view()]))


def test_five_of_five_challenges_is_yes_one_hundred_percent() -> None:
    now = datetime.now(UTC)
    rec = record(reads=5, challenges=5, last_state="YES")
    text = render.render_bin(rec, [gate_view()], now)
    assert "YES · 5/5 challenged · 100%" in text
    assert "low n" not in text


def test_passive_render_is_authoritative_and_exact() -> None:
    now = datetime.now(UTC)
    rec = record(reads=6, challenges=5, last_state="YES")
    rec.r3ds.last_at = now - timedelta(hours=3)
    text = render.render_bin(rec, [gate_view()], now)
    assert "YES · 5/6 challenged · 83% · 3h" in text
    assert "PROBE" not in text
    assert render.PROBE_TOOLTIP not in text


def test_probe_render_is_labelled_as_a_gate_proxy_with_tooltip() -> None:
    now = datetime.now(UTC)
    rec = record(reads=0)
    rec.r3ds.probe_reads = 1
    rec.r3ds.probe_challenges = 1
    rec.r3ds.probe_last_at = now - timedelta(hours=4)
    rec.r3ds.probe_last_state = "YES"
    rec.r3ds.source = "probe"
    text = render.render_bin(rec, [gate_view()], now)
    assert "PROBE YES · gate proxy · 4h ⓘ" in text
    assert render.PROBE_TOOLTIP in text
    assert "YES · 1/1 challenged" not in text


def test_recent_probe_renders_free_reprobe_copy() -> None:
    now = datetime.now(UTC)
    rec = record(reads=0)
    rec.r3ds.probe_reads = 1
    rec.r3ds.probe_challenges = 0
    rec.r3ds.probe_last_at = now - timedelta(minutes=12)
    rec.r3ds.probe_last_state = "NO"
    text = render.render_bin(rec, [gate_view()], now)
    assert "measured 12m ago · re-probe free" in text
    labels = buttons(kb.bin_result(rec, [gate_view()]))
    assert "🔐 Re-probe 3DS · free" in labels


def test_both_channels_zero_is_unmeasured_with_measure_button() -> None:
    now = datetime.now(UTC)
    rec = record(reads=0)
    text = render.render_bin(rec, [gate_view()], now)
    assert "UNMEASURED  ▶ measure" in text
    assert "PROBE" not in text
    assert "ⓘ" not in text
    labels = buttons(kb.bin_result(rec, [gate_view()]))
    assert "🔐 Measure 3DS · 1cr" in labels


def test_render_bin_escapes_every_interpolated_value() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=2, fetched_at=now)
    rec.bin = "44654205"
    rec.brand = "<b>visa</b>"
    rec.type = "credit<img src=x>"
    rec.tier = "platinum & gold"
    rec.bank_short = "<script>alert(1)</script>"
    rec.bank_phone = "<i>phone</i>"
    rec.bank_site = "site?a=1&b=2"
    rec.country = "<b>United States</b>"
    rec.flags = ["<b>flag</b>"]
    gate = gate_view(label="<b>Gate</b>")
    text = render.render_bin(rec, [gate], now)
    assert "<b>visa</b>" not in text
    assert "<script>" not in text
    assert "<img" not in text
    assert "&lt;B&gt;VISA&lt;/B&gt;" in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;b&gt;Gate&lt;/b&gt;" in text


def test_sub_screens_escape_every_interpolated_value() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=1, fetched_at=now)
    rec.type = "<script>type</script>"
    rec.bank_short = "<b>bank</b>"
    rec.sources = [
        FieldSource(source="<i>src</i>", field="bank_short", value="<script>v</script>")
    ]
    gate = gate_view(label="<b>Gate</b>")
    screens = [
        render.render_evidence(rec, now),
        render.render_provenance(rec, now),
        render.render_doctor(rec, [gate], now),
        render.render_bin_gates(rec, [gate], now),
        render.render_bin_share(rec, [gate], now),
    ]
    for screen in screens:
        assert "<script>" not in screen
        assert "<b>Gate</b>" not in screen

    provenance = render.render_provenance(rec, now)
    assert "&lt;script&gt;v&lt;/script&gt;" in provenance
    assert "&lt;i&gt;src&lt;/i&gt;" in provenance
    doctor = render.render_doctor(rec, [gate], now)
    assert "&lt;SCRIPT&gt;TYPE&lt;/SCRIPT&gt;" in doctor
    gates_screen = render.render_bin_gates(rec, [gate], now)
    share_screen = render.render_bin_share(rec, [gate], now)
    for screen in (gates_screen, share_screen):
        assert "&lt;b&gt;Gate&lt;/b&gt;" in screen


def test_stale_row_swaps_run_for_refresh() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=2, fetched_at=now - timedelta(days=8))
    labels = buttons(kb.bin_result(rec, [gate_view()]))
    assert any(label.startswith("🔄 Refresh sources") for label in labels)
    assert not any(label.startswith("▶️ Check on") for label in labels)


def test_render_twice_is_byte_identical() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=2, fetched_at=now - timedelta(hours=3))
    first = render.render_bin(rec, [gate_view()], now)
    second = render.render_bin(rec, [gate_view()], now)
    assert first == second


def test_dw_and_pad_handle_emoji_and_cjk() -> None:
    assert render.dw("BIN") == 3
    assert render.dw("🔍") == 2
    assert render.dw("銀行") == 4
    assert render.dw(render.pad("BIN")) == 15
    assert render.dw(render.pad("銀行", 8)) == 8


def test_main_screen_has_one_confidence_chip_and_no_field_rows() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=2, fetched_at=now)
    text = render.render_bin(rec, [gate_view()], now)
    assert text.count("📶") == 1
    assert "42%" not in text
    assert "🏦 bank" not in text
    assert "📚 2 sources" in text


def test_avs_always_carries_its_basis() -> None:
    now = datetime.now(UTC)
    for iso2 in ("US", "DE", "??"):
        rec = record(sources_n=2, fetched_at=now)
        rec.iso2 = iso2
        text = render.render_bin(rec, [gate_view()], now)
        assert "derived from issuer country" in text


def test_each_icon_appears_once_on_the_main_screen() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=2, fetched_at=now)
    text = render.render_bin(rec, [gate_view()], now)
    icons = (
        "🔍",
        "📶",
        "ℹ️",
        "🔐",
        "📍",
        "📮",
        "🛡️",
        "💳",
        render.ICONS["TIER"],
        render.ICONS["TYPE"],
        render.ICONS["COMMERCIAL"],
        render.ICONS["PREPAID"],
        render.ICONS["DEBIT"],
        "🏛",
        "☎️",
        "🔗",
        "🔢",
        "🎯",
        "🚩",
        "👁️",
        "📚",
        "🏷️",
    )
    for icon in icons:
        assert text.count(icon) == 1, icon
    assert len(set(icons)) == len(icons)


def test_icons_are_unique_across_every_bin_screen() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=2, fetched_at=now)
    rec.sources = [
        FieldSource(source="binlist", field="type", value="credit"),
        FieldSource(source="antiplist", field="tier", value="platinum"),
        FieldSource(source="binlist", field="prepaid", value="false"),
        FieldSource(source="binlist", field="commercial", value="false"),
        FieldSource(source="binlist", field="debit", value="false"),
    ]
    screens = [
        render.render_bin(rec, [gate_view()], now),
        render.render_evidence(rec, now),
        render.render_provenance(rec, now),
        render.render_doctor(rec, [gate_view()], now),
        render.render_bin_gates(rec, [gate_view()], now),
        render.render_bin_share(rec, [gate_view()], now),
    ]
    for screen in screens:
        for icon in render.ICONS.values():
            assert screen.count(icon) <= 1, (icon, screen)


def test_right_column_columns_line_up() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=2, fetched_at=now)
    text = render.render_bin(rec, [gate_view()], now)
    lines = text.splitlines()
    pairs = [
        ("💳", render.ICONS["TIER"]),
        (render.ICONS["TYPE"], render.ICONS["COMMERCIAL"]),
        (render.ICONS["PREPAID"], render.ICONS["DEBIT"]),
    ]
    indexes = []
    for first, second in pairs:
        line = next(line for line in lines if first in line and second in line)
        indexes.append(line.index(second))
    assert len(set(indexes)) == 1, indexes


def test_identity_values_are_uppercase() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=2, fetched_at=now)
    text = render.render_bin(rec, [gate_view()], now)
    assert "VISA" in text
    assert "PLATINUM" in text
    assert "CREDIT" in text
    assert "LUHN OK" in text
    assert "visa" not in text
    assert "platinum" not in text
    assert "credit" not in text


def test_flags_report_zero_hits_and_inconsistent_3ds() -> None:
    rec = record(
        sources_n=2,
        reads=4,
        challenges=2,
        last_state="YES",
        attempts=10,
        approved=0,
    )
    flags = render.record_flags(rec)
    assert "0 hits in 10" in flags
    assert "3DS inconsistent" in flags

    rec.hits.approved = 1
    assert "0 hits in 10" not in render.record_flags(rec)

    rec.hits.approved = 0
    rec.r3ds.challenges = 0
    assert "3DS inconsistent" not in render.record_flags(rec)


def test_low_reads_appends_low_n() -> None:
    now = datetime.now(UTC)
    rec = record(reads=2, challenges=1, last_state="YES")
    rec.r3ds.last_at = now - timedelta(hours=1)
    text = render.render_bin(rec, [gate_view()], now)
    assert "YES · 1/2 challenged · 50% · 1h  ⚠️ low n" in text


def test_exactly_one_copy_button() -> None:
    rec = record(sources_n=2)
    labels = buttons(kb.bin_result(rec, [gate_view()]))
    assert sum(1 for label in labels if label.startswith("📋")) == 1


def test_evidence_screen_reaches_source_rows() -> None:
    markup = kb.bin_evidence("44654205")
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "bin:src:44654205" in callbacks


def test_gate_block_is_ranked_and_priced() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=2, fetched_at=now)
    gates = [
        gate_view("auth_stripe", "Stripe Auth", cost="1", p50=1200),
        gate_view("auth_adyen", "Adyen Auth", cost="2", p50=2400),
        gate_view("auth_open", "Open Auth", state="OPEN", cost="9"),
    ]
    text = render.render_bin(rec, gates, now)
    assert "Stripe Auth 1cr (3DS) · Adyen Auth 2cr (3DS)" in text
    assert "Open Auth" not in text


def test_provenance_and_evidence_use_the_same_record() -> None:
    now = datetime.now(UTC)
    rec = record(sources_n=1, fetched_at=now - timedelta(days=2))
    evidence = render.render_evidence(rec, now)
    provenance = render.render_provenance(rec, now)
    assert "EVIDENCE" in evidence
    assert "PROVENANCE" in provenance
    assert rec.bin in evidence and rec.bin in provenance
