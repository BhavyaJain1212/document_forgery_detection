"""Robust numeric-cell parsing for invoice tables.

Handles the number shapes that show up on Indian + Western insurance/hospital
bills:

    - currency prefixes/suffixes: ``Rs``, ``Rs.``, ``INR``, ``₹``, ``$``, ``Rupees``
    - Western thousands grouping: ``1,234,567.89``
    - Indian grouping: ``1,00,000.00`` (groups of two after the first three)
    - plain decimals / integers: ``83.23`` / ``9`` / ``.50``
    - parenthesised negatives: ``(1,200.00)`` -> ``-1200.0``
    - a trailing percent is rejected (a rate %, not a money amount)

Both grouping styles use the comma purely as a thousands separator with ``.`` as
the decimal point, so removing every comma reconciles both; we only parse cells
that, after stripping currency noise, are a clean signed decimal.
"""

from __future__ import annotations

import re
import unicodedata

# A clean number AFTER currency stripping and comma removal:
#   optional sign, digits with optional single decimal point, or bare ".5".
_CLEAN_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")

# Currency markers / words removed before parsing (case-insensitive).
_CURRENCY_RE = re.compile(
    r"(?:₹|\$|£|€|rs\.?|inr|rupees?|usd)",
    re.IGNORECASE,
)

# A comma that sits between digits (grouping separator). We only strip these so
# we never silently merge unrelated tokens.
_GROUPING_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")


def normalize_label(text: str) -> str:
    """Normalise a header label to compare against :class:`InvoiceConfig` roles.

    NFC, lower-case, keep only ``[a-z0-9]`` (drops spaces, slashes, punctuation).
    ``"Unit Price"`` / ``"unit-price"`` / ``"UnitPrice"`` -> ``"unitprice"``.
    """
    nfc = unicodedata.normalize("NFC", text)
    return re.sub(r"[^a-z0-9]", "", nfc.lower())


def parse_amount(text: str) -> float | None:
    """Parse a numeric cell to ``float``; return ``None`` if it is not numeric.

    Strips currency symbols/words and grouping commas, supports parenthesised
    negatives, and rejects percentages and anything that is not a clean decimal
    after cleaning (so labels and IDs never parse as amounts).
    """
    if text is None:
        return None
    s = unicodedata.normalize("NFC", text).strip()
    if not s:
        return None

    # Percentages are rates, not money amounts — never an arithmetic operand.
    if s.endswith("%"):
        return None

    negative = False
    # Parenthesised negative, e.g. accounting style "(1,200.00)".
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    s = _CURRENCY_RE.sub("", s).strip()
    # A leading minus may survive currency stripping (e.g. "-Rs 50").
    if s.startswith("-"):
        negative = not negative
        s = s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()

    s = _GROUPING_COMMA_RE.sub("", s)
    s = s.replace(" ", "")

    if not _CLEAN_NUMBER_RE.match(s):
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def is_numeric_cell(text: str) -> bool:
    """True if *text* parses as an amount."""
    return parse_amount(text) is not None
