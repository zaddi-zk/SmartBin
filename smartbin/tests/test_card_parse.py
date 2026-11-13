"""Card parser contract: every common paste format must parse."""
from __future__ import annotations

import unittest

from models import Card


class CardParseTest(unittest.TestCase):
    def test_pipe_two_digit_year(self):
        card = Card.parse("4242424242424242|12|30|123")
        self.assertEqual(card.number, "4242424242424242")
        self.assertEqual((card.exp_month, card.exp_year), (12, 2030))
        self.assertEqual(card.cvv, "123")

    def test_space_and_slash(self):
        card = Card.parse("4242424242424242 11/29 716")
        self.assertEqual((card.exp_month, card.exp_year), (11, 2029))
        self.assertEqual(card.cvv, "716")

    def test_semicolon_four_digit_year(self):
        card = Card.parse("4242424242424242;11;2029;716")
        self.assertEqual((card.exp_month, card.exp_year), (11, 2029))

    def test_colon_separator(self):
        card = Card.parse("4242424242424242:11:29:716")
        self.assertEqual(card.cvv, "716")

    def test_comma_separator(self):
        card = Card.parse("4242424242424242,11,29,716")
        self.assertEqual(card.number, "4242424242424242")

    def test_grouped_pan(self):
        card = Card.parse("4242 4242 4242 4242 11/29 716")
        self.assertEqual(card.number, "4242424242424242")

    def test_trailing_zip_is_ignored(self):
        card = Card.parse("4242424242424242|11|29|716|90210")
        self.assertEqual(card.cvv, "716")

    def test_extra_whitespace(self):
        card = Card.parse("  4242424242424242   11 / 29   716  ")
        self.assertEqual(card.number, "4242424242424242")
        self.assertEqual((card.exp_month, card.exp_year), (11, 2029))

    def test_luhn_failure_rejected(self):
        with self.assertRaises(ValueError):
            Card.parse("4242424242424241 11/29 716")

    def test_missing_cvv_rejected(self):
        with self.assertRaises(ValueError):
            Card.parse("4242424242424242 11/29")


if __name__ == "__main__":
    unittest.main()
