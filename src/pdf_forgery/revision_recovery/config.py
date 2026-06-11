"""Central configuration for Stage 1 (revision recovery).

All scoring thresholds, score bands, high-value regex patterns, and
normalisation toggles live here.  Pass a :class:`Config` instance to every
public function that accepts one; pass ``None`` to use the spec defaults.

Nothing in scoring, textdiff, or normalisation should hard-code a threshold or
regex — every magic constant is defined here and exposed for override.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Default regex pattern strings
# ---------------------------------------------------------------------------
# These strings are the canonical source of truth for the compiled patterns
# in highvalue.py.  Override them in a Config instance to customise matching.

#: Amount / currency (strong high-value signal).
#: Optional ₹ / $ / Rs / INR prefix, then comma-grouped or plain number.
#: Also matches standalone currency symbols used as separate tokens.
DEFAULT_AMOUNT_PATTERN: str = (
    r"^(?:[₹$]|Rs|INR)?"
    r"(?:\d{1,3}(?:,\d{2,3})+(?:\.\d+)?"  # comma-grouped: 5,000 / 1,20,000.00
    r"|\d+(?:\.\d+)?)"                      # plain: 50000 / 100.50
    r"$"
    r"|^(?:[₹$]|Rs|INR)$"                  # standalone currency marker
)

#: Date (strong high-value signal).
#: dd/mm/yyyy, dd-mm-yyyy, ISO 8601 yyyy-mm-dd.
DEFAULT_DATE_PATTERN: str = (
    r"^(?:"
    r"\d{1,2}[/\-]\d{1,2}[/\-]\d{4}"  # dd/mm/yyyy or dd-mm-yyyy
    r"|\d{4}-\d{2}-\d{2}"              # ISO 8601
    r")$"
)

#: ID-like (weak high-value signal).
#: Any run of ≥ 6 consecutive alphanumeric characters (policy / claim numbers).
DEFAULT_ID_LIKE_PATTERN: str = r"[A-Za-z0-9]{6,}"


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """All tunable parameters for Stage 1 scoring, diffing, and normalisation.

    Pass an instance to the public API functions; pass ``None`` to get
    spec-compliant defaults (equivalent to ``Config()``).
    """

    # ------------------------------------------------------------------ #
    # High-value regex patterns (raw strings; compiled on first use)      #
    # ------------------------------------------------------------------ #

    amount_pattern: str = DEFAULT_AMOUNT_PATTERN
    """Regex for currency/amount tokens (strong booster). Full-match applied."""

    date_pattern: str = DEFAULT_DATE_PATTERN
    """Regex for date tokens (strong booster). Full-match applied."""

    id_like_pattern: str = DEFAULT_ID_LIKE_PATTERN
    """Regex for ID-like tokens (weak booster). Search (not full-match) applied."""

    # ------------------------------------------------------------------ #
    # High-value classification / scoring toggles                         #
    # ------------------------------------------------------------------ #

    enable_amount_pattern: bool = True
    """When False, AMOUNT matches are ignored by scoring (treated as prose)."""

    enable_date_pattern: bool = True
    """When False, DATE matches are ignored by scoring (treated as prose)."""

    enable_id_like_boost: bool = True
    """When False, ID_LIKE matches do not boost score above the prose band."""

    # ------------------------------------------------------------------ #
    # Normalisation toggles                                               #
    # ------------------------------------------------------------------ #

    nfc_normalize: bool = True
    """Apply Unicode NFC normalisation before all comparisons."""

    strip_zero_width: bool = True
    """Strip U+200B/C/D (zero-width chars) and U+00AD (soft hyphen)."""

    collapse_whitespace: bool = True
    """Collapse all whitespace runs to a single space and trim ends."""

    # ------------------------------------------------------------------ #
    # Score values within each band                                       #
    #   HIGH    70-100                                                    #
    #   MEDIUM  30-70                                                     #
    #   LOW      0-30                                                     #
    # ------------------------------------------------------------------ #

    score_high_amount_date: int = 95
    """HIGH score when AMOUNT or DATE token changed (90-100 sub-band)."""

    score_high_id_like: int = 85
    """HIGH score when only ID_LIKE matched (70-85 sub-band; weaker evidence)."""

    score_high_prose: int = 75
    """HIGH score when no high-value pattern matched (prose-only, 70-85 sub-band)."""

    score_medium_content_no_text: int = 60
    """MEDIUM score when a CONTENT stream changed but the text diff is empty."""

    score_medium_overlay: int = 55
    """MEDIUM score when an OVERLAY object (stamp/redaction annotation) changed."""

    score_medium_field_edit: int = 50
    """MEDIUM score when a form field was edited from a prior non-empty value."""

    score_medium_form_fill: int = 45
    """MEDIUM score when form_fill_triggers_medium=True and a blank field was filled."""

    score_medium_recon_failure: int = 40
    """MEDIUM score when a revision was detected but could not be reconstructed."""

    score_low_default: int = 15
    """LOW score (benign multi-revision changes — signing, annotations, metadata)."""

    # ------------------------------------------------------------------ #
    # Behaviour toggles                                                   #
    # ------------------------------------------------------------------ #

    form_fill_triggers_medium: bool = False
    """If True, FORM_FILL (empty→filled) routes to MEDIUM instead of LOW.

    Default False: filling a previously blank field is typically a legitimate
    first-time use (e.g. a user completing a form), not a forgery signal.
    Set True for stricter environments where any field modification is suspicious.
    """
