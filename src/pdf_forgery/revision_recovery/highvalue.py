"""Structured high-value token classification with legacy compatibility.

Revision recovery historically classified tokens into only AMOUNT / DATE /
ID_LIKE.  Font forensics needs more nuance: a bare digit run may be an amount,
an identifier, a quantity, or simply an unknown numeric token depending on its
shape and nearby labels.  ``classify_token`` therefore returns evidence-bearing
``TokenClassification`` objects while ``classify_token_kind`` preserves the
original enum-only behaviour for callers whose scoring rubric depends on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .models import HighValueKind


class TokenCandidate(str, Enum):
    """Semantic candidates considered for a token."""

    AMOUNT = "amount"
    IDENTIFIER = "identifier"
    DATE = "date"
    QUANTITY = "quantity"
    UNKNOWN_NUMERIC = "unknown_numeric"


class ClassificationStrength(str, Enum):
    """How strongly the available token/context evidence supports the result."""

    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"


@dataclass(frozen=True, eq=False)
class TokenClassification:
    """Structured classification and the signals that produced it.

    ``legacy_kind`` deliberately preserves the old regex-priority result.  The
    ``value``/comparison helpers keep older lightweight callers working while
    new callers should use ``primary``, ``strength``, ``signals``, and
    ``high_value_kind``.
    """

    token: str
    primary: TokenCandidate
    candidates: tuple[TokenCandidate, ...]
    strength: ClassificationStrength
    signals: tuple[str, ...]
    legacy_kind: HighValueKind | None

    @property
    def high_value_kind(self) -> HighValueKind | None:
        """Context-aware high-value kind, if the primary candidate has one."""
        if self.primary is TokenCandidate.AMOUNT:
            return HighValueKind.AMOUNT
        if self.primary is TokenCandidate.DATE:
            return HighValueKind.DATE
        if self.primary is TokenCandidate.IDENTIFIER:
            return HighValueKind.ID_LIKE
        return None

    @property
    def value(self) -> str:
        """Compatibility value used by older report adapters."""
        kind = self.legacy_kind or self.high_value_kind
        return kind.value if kind is not None else self.primary.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, HighValueKind):
            return self.legacy_kind is other
        if isinstance(other, TokenClassification):
            return (
                self.token,
                self.primary,
                self.candidates,
                self.strength,
                self.signals,
                self.legacy_kind,
            ) == (
                other.token,
                other.primary,
                other.candidates,
                other.strength,
                other.signals,
                other.legacy_kind,
            )
        return NotImplemented

    def __hash__(self) -> int:
        # Match the legacy enum hash so membership checks in older callers keep
        # behaving as before without forcing stage-specific changes.
        return hash(self.legacy_kind) if self.legacy_kind is not None else hash(self.primary)


# Legacy rubric patterns.  Keep these semantics stable for revision recovery.
_AMOUNT_RE = re.compile(
    r"^(?:[₹$]|Rs|INR)?"
    r"(?:\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)$"
    r"|^(?:[₹$]|Rs|INR)$",
)
_DATE_RE = re.compile(
    r"^(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{4}|\d{4}-\d{2}-\d{2})$"
)
_ID_LIKE_RE = re.compile(r"[A-Za-z0-9]{6,}")

_CURRENCY_RE = re.compile(r"(?:₹|\$|\bRs\.?\b|\bINR\b)", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:,\d+)*)(?:\.\d+)?$")
_TWO_DECIMAL_RE = re.compile(r"^[+-]?\d+(?:,\d+)*\.\d{2}$")
_MONETARY_GROUPING_RE = re.compile(
    r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d{2})?$"
)
_LONG_DIGIT_RE = re.compile(r"^\d{8,}$")
_ALNUM_ID_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9._/-]{6,}$")

_MONEY_LABEL_RE = re.compile(
    r"\b(?:amount|total|due|price|balance|subtotal|payable|grand\s+total|net\s+amount)\b",
    re.IGNORECASE,
)
_IDENTIFIER_LABEL_RE = re.compile(
    r"\b(?:abn|gstin|tax\s*id|account(?:\s*(?:no|number))?|invoice\s*(?:no|number)|"
    r"phone|mobile|policy\s*(?:no|number)|claim\s*(?:no|number))\b",
    re.IGNORECASE,
)
_QUANTITY_LABEL_RE = re.compile(
    r"\b(?:qty|quantity|units?|hours?|days?)\b", re.IGNORECASE
)

_KIND_PRIORITY: dict[HighValueKind, int] = {
    HighValueKind.AMOUNT: 0,
    HighValueKind.DATE: 1,
    HighValueKind.ID_LIKE: 2,
}


def _context_text(context: str | Iterable[str] | None) -> str:
    if context is None:
        return ""
    if isinstance(context, str):
        return context
    return " ".join(str(part) for part in context)


def _legacy_kind(token: str) -> HighValueKind | None:
    """Original AMOUNT > DATE > ID_LIKE classification, unchanged."""
    if _AMOUNT_RE.fullmatch(token):
        return HighValueKind.AMOUNT
    if _DATE_RE.fullmatch(token):
        return HighValueKind.DATE
    if _ID_LIKE_RE.search(token):
        return HighValueKind.ID_LIKE
    return None


def classify_token(
    token: str,
    *,
    context: str | Iterable[str] | None = None,
) -> TokenClassification | None:
    """Classify *token* using token shape plus optional nearby text context.

    Monetary evidence includes currency markers, exactly two decimals, monetary
    comma grouping, and nearby amount/total labels.  Identifier evidence includes
    identifier labels and long unseparated digit runs with no currency/decimal.
    Non-matching prose still returns ``None`` for compatibility.
    """
    text = token.strip()
    if not text:
        return None

    nearby = _context_text(context)
    legacy = _legacy_kind(text)
    signals: list[str] = []
    candidates: list[TokenCandidate] = []

    if _DATE_RE.fullmatch(text):
        return TokenClassification(
            token=text,
            primary=TokenCandidate.DATE,
            candidates=(TokenCandidate.DATE,),
            strength=ClassificationStrength.STRONG,
            signals=("date_pattern",),
            legacy_kind=legacy,
        )

    has_currency = bool(_CURRENCY_RE.search(text))
    numeric_text = _CURRENCY_RE.sub("", text).strip()
    is_numeric = bool(_NUMERIC_RE.fullmatch(numeric_text))
    has_two_decimals = bool(_TWO_DECIMAL_RE.fullmatch(numeric_text))
    has_money_grouping = bool(_MONETARY_GROUPING_RE.fullmatch(numeric_text))
    has_money_label = bool(_MONEY_LABEL_RE.search(nearby))
    has_identifier_label = bool(_IDENTIFIER_LABEL_RE.search(nearby))
    has_quantity_label = bool(_QUANTITY_LABEL_RE.search(nearby))
    is_long_digits = bool(_LONG_DIGIT_RE.fullmatch(numeric_text))
    is_alnum_id = bool(_ALNUM_ID_RE.fullmatch(text))

    if has_currency:
        signals.append("currency_marker")
    if has_two_decimals:
        signals.append("two_decimal_places")
    if has_money_grouping:
        signals.append("monetary_comma_grouping")
    if has_money_label:
        signals.append("money_label")
    if has_identifier_label:
        signals.append("identifier_label")
    if has_quantity_label:
        signals.append("quantity_label")
    if is_long_digits:
        signals.append("long_unseparated_digit_run")
    if is_numeric and "." not in numeric_text:
        signals.append("no_decimal")
    if is_numeric and not has_currency:
        signals.append("no_currency_marker")
    if is_alnum_id:
        signals.append("alphanumeric_identifier")

    # Intrinsic monetary formatting outranks labels when it is unambiguous.
    if has_currency or has_two_decimals or has_money_grouping:
        candidates.append(TokenCandidate.AMOUNT)
        if is_numeric:
            candidates.append(TokenCandidate.UNKNOWN_NUMERIC)
        return TokenClassification(
            text,
            TokenCandidate.AMOUNT,
            tuple(candidates),
            ClassificationStrength.STRONG,
            tuple(signals),
            legacy,
        )

    # Explicit local labels resolve otherwise ambiguous bare digit runs.
    if has_identifier_label and not has_money_label and (is_numeric or is_alnum_id):
        candidates.extend((TokenCandidate.IDENTIFIER, TokenCandidate.UNKNOWN_NUMERIC))
        if is_numeric:
            candidates.append(TokenCandidate.AMOUNT)
        return TokenClassification(
            text,
            TokenCandidate.IDENTIFIER,
            tuple(dict.fromkeys(candidates)),
            ClassificationStrength.STRONG,
            tuple(signals),
            legacy,
        )

    if has_money_label and is_numeric:
        candidates.extend((TokenCandidate.AMOUNT, TokenCandidate.UNKNOWN_NUMERIC))
        return TokenClassification(
            text,
            TokenCandidate.AMOUNT,
            tuple(candidates),
            ClassificationStrength.STRONG,
            tuple(signals),
            legacy,
        )

    if has_quantity_label and is_numeric:
        candidates.extend((TokenCandidate.QUANTITY, TokenCandidate.UNKNOWN_NUMERIC))
        return TokenClassification(
            text,
            TokenCandidate.QUANTITY,
            tuple(candidates),
            ClassificationStrength.MEDIUM,
            tuple(signals),
            legacy,
        )

    if is_long_digits or is_alnum_id:
        candidates.append(TokenCandidate.IDENTIFIER)
        if is_numeric:
            candidates.extend((TokenCandidate.UNKNOWN_NUMERIC, TokenCandidate.AMOUNT))
        return TokenClassification(
            text,
            TokenCandidate.IDENTIFIER,
            tuple(dict.fromkeys(candidates)),
            ClassificationStrength.MEDIUM,
            tuple(signals),
            legacy,
        )

    if is_numeric:
        return TokenClassification(
            text,
            TokenCandidate.UNKNOWN_NUMERIC,
            (TokenCandidate.UNKNOWN_NUMERIC, TokenCandidate.AMOUNT),
            ClassificationStrength.WEAK,
            tuple(signals),
            legacy,
        )

    if legacy is HighValueKind.ID_LIKE:
        return TokenClassification(
            text,
            TokenCandidate.IDENTIFIER,
            (TokenCandidate.IDENTIFIER,),
            ClassificationStrength.WEAK,
            tuple(signals or ["id_like_pattern"]),
            legacy,
        )
    return None


def classify_token_kind(token: str) -> HighValueKind | None:
    """Return the legacy enum-only result used by revision-recovery scoring."""
    result = classify_token(token)
    return result.legacy_kind if result is not None else None


def classify_change(before: str, after: str) -> HighValueKind | None:
    """Return the highest-priority legacy kind found on either side of a change."""
    kinds = [
        kind
        for kind in (classify_token_kind(before), classify_token_kind(after))
        if kind is not None
    ]
    if not kinds:
        return None
    return min(kinds, key=lambda kind: _KIND_PRIORITY[kind])


__all__ = [
    "ClassificationStrength",
    "TokenCandidate",
    "TokenClassification",
    "classify_change",
    "classify_token",
    "classify_token_kind",
]
