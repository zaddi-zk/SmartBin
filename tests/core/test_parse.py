from __future__ import annotations

from decimal import Decimal

import pytest
from app.core.parse import Card, CardError, fmt_cr, fmt_minor, mask_pan, parse_amount_to_minor


def test_pipe_two_digit_year() -> None:
    card = Card.parse("4242424242424242|12|30|123")
    assert card.number == "4242424242424242"
    assert (card.exp_month, card.exp_year) == (12, 2030)
    assert card.cvv == "123"


def test_space_and_slash() -> None:
    card = Card.parse("4242424242424242 11/29 716")
    assert (card.exp_month, card.exp_year) == (11, 2029)
    assert card.cvv == "716"


def test_semicolon_four_digit_year() -> None:
    card = Card.parse("4242424242424242;11;2029;716")
    assert card.exp_year == 2029


def test_colon_separator() -> None:
    assert Card.parse("4242424242424242:11:29:716").cvv == "716"


def test_comma_separator() -> None:
    assert Card.parse("4242424242424242,11,29,716").number == "4242424242424242"


def test_grouped_pan() -> None:
    assert Card.parse("4242 4242 4242 4242 11/29 716").number == "4242424242424242"


def test_trailing_zip() -> None:
    card = Card.parse("4242424242424242|11|29|716|90210")
    assert card.cvv == "716"
    assert card.postal == "90210"


def test_extra_whitespace() -> None:
    card = Card.parse("  4242424242424242   11 / 29   716  ")
    assert card.number == "4242424242424242"
    assert (card.exp_month, card.exp_year) == (11, 2029)


def test_luhn_failure_rejected() -> None:
    with pytest.raises(CardError):
        Card.parse("4242424242424241 11/29 716")


def test_missing_cvv_rejected() -> None:
    with pytest.raises(CardError):
        Card.parse("4242424242424242 11/29")


def test_expired_rejected() -> None:
    with pytest.raises(CardError):
        Card.parse("4242424242424242 01/20 716")


def test_mask_pan() -> None:
    assert mask_pan("4465420596589283") == "4465…9283"


def test_amount_is_minor_units() -> None:
    assert parse_amount_to_minor("1") == 100
    assert parse_amount_to_minor("0.50") == 50
    assert parse_amount_to_minor("10,5") == 1050


def test_amount_rejects_sub_cent() -> None:
    with pytest.raises(CardError):
        parse_amount_to_minor("1.001")


def test_fmt_helpers() -> None:
    assert fmt_cr(Decimal("250.00")) == "250"
    assert fmt_cr(Decimal("0.5")) == "0.5"
    assert fmt_minor(150) == "$1.50"
