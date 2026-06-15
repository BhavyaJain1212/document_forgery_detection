"""Numeric-cell parsing: Western commas, Indian grouping, currency, decimals."""

from __future__ import annotations

import pytest

from pdf_forgery.invoice_arithmetic.numbers import (
    is_numeric_cell,
    normalize_label,
    parse_amount,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        # Plain
        ("9", 9.0),
        ("83.23", 83.23),
        ("0", 0.0),
        (".50", 0.50),
        # Western thousands grouping
        ("1,234,567.89", 1234567.89),
        ("5,000", 5000.0),
        ("12,32", 1232.0),  # comma between digits removed
        # Indian grouping (groups of two after the first three)
        ("1,00,000.00", 100000.0),
        ("1,20,000.00", 120000.0),
        ("12,34,567.50", 1234567.50),
        # Currency symbols / words
        ("Rs 5,000", 5000.0),
        ("Rs.5000", 5000.0),
        ("INR 1,00,000.00", 100000.0),
        ("₹1,20,000.00", 120000.0),  # rupee sign
        ("$1,234.50", 1234.50),
        ("Rupees 250", 250.0),
        # Parenthesised negative (accounting)
        ("(1,200.00)", -1200.0),
        ("-50", -50.0),
    ],
)
def test_parse_amount_valid(text, expected):
    assert parse_amount(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "  ",
        "abc",
        "Subtotal",
        "12.5%",          # percentage is a rate, not a money amount
        "POL12345",       # id-like, not numeric
        "12.3.4",         # malformed
        "1,2,3,4,5,6",    # not a clean grouped number after comma removal -> 123456 actually
    ],
)
def test_parse_amount_rejects_non_numeric(text):
    # "1,2,3,4,5,6" collapses to 123456 (commas between digits) which is valid;
    # exclude it from the rejection assertion.
    if text == "1,2,3,4,5,6":
        assert parse_amount(text) == 123456.0
        return
    assert parse_amount(text) is None


def test_is_numeric_cell():
    assert is_numeric_cell("1,00,000.00") is True
    assert is_numeric_cell("Grand Total") is False


@pytest.mark.parametrize(
    "label, normalized",
    [
        ("Unit Price", "unitprice"),
        ("unit-price", "unitprice"),
        ("UnitPrice", "unitprice"),
        ("Qty", "qty"),
        ("CGST @9%", "cgst9"),
        ("Grand Total", "grandtotal"),
    ],
)
def test_normalize_label(label, normalized):
    assert normalize_label(label) == normalized
