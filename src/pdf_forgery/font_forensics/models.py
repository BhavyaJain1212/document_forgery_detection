"""Pure data models for the font-forensics stage.

These mirror the spirit of ``revision_recovery.models``: frozen dataclasses with
no detection logic. The detectors in :mod:`detect` produce :class:`FontFinding`
objects; :mod:`analyze` aggregates them into a :class:`FontReport`; the adapter
maps that report onto the shared :class:`~pdf_forgery.core.types.StageResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..core.glyphs import Glyph, TextLine, Token
from ..core.types import ConfidenceTier
from ..revision_recovery.models import HighValueKind

# ``Glyph`` / ``Token`` / ``TextLine`` are the shared, stage-agnostic glyph
# models defined in ``core.glyphs`` (and used by revision_recovery's fallback
# extractor too). They are re-exported here so existing
# ``font_forensics.models`` imports keep working unchanged.

__all__ = [
    "Glyph",
    "TextLine",
    "Token",
    "FontFindingKind",
    "FontFinding",
    "FontReport",
    "ConfidenceTier",
    "HighValueKind",
]


class FontFindingKind(str, Enum):
    """Why a font finding was raised (drives its tier)."""

    HIGH_VALUE_SUBSET_SPLIT = "high_value_subset_split"
    """A high-value token carries a different subset tag of the SAME base face
    than the rest of its line — the re-embedding fingerprint. -> HIGH."""

    HIGH_VALUE_SUBSTITUTION = "high_value_substitution"
    """A high-value token is set in a different font FAMILY than its line
    context (not a mere style variant) — font substitution. -> HIGH."""

    HIGH_VALUE_BASELINE_DEVIATION = "high_value_baseline_deviation"
    """A high-value token on an otherwise-uniform line is set in a different
    family than the document baseline (no line context to compare). -> MEDIUM."""

    INTRA_LINE_SUBSET_SPLIT = "intra_line_subset_split"
    """A same-base / different-subset-tag split inside a line, NOT overlapping a
    high-value token. -> MEDIUM."""

    INTRA_TOKEN_FONT_MIX = "intra_token_font_mix"
    """One or more *minority* glyphs INSIDE a single token use a different font
    family, or the same base face with a different subset tag, than the token's
    majority font — the single-glyph-insertion fingerprint (e.g. a '0' typed
    into an amount in a different font). -> HIGH for amount/date/ID tokens,
    MEDIUM for prose tokens (downgraded when intra-token mixing is pervasive)."""


@dataclass(frozen=True)
class FontFinding:
    """One flagged font inconsistency, with everything a reviewer needs."""

    page_index: int
    kind: FontFindingKind
    tier: ConfidenceTier
    token: str
    token_font: str
    context_font: str
    """The font the token conflicts with (line-dominant font, or doc baseline)."""

    bbox: tuple[float, float, float, float]
    reason: str
    high_value: HighValueKind | None = None
    conflicting_fonts: tuple[str, ...] = ()
    """All distinct fonts involved in the conflict, sorted for determinism."""

    # --- Intra-token-mix evidence (populated only for INTRA_TOKEN_FONT_MIX) --- #
    minority_font: str = ""
    """The foreign font carried by the suspicious minority glyph(s) inside the
    token (e.g. ``SUMSRI+SourceSansPro-Regular``). Empty for other finding kinds."""

    suspicious_text: str = ""
    """The suspicious character(s) themselves (e.g. ``"0"``)."""

    suspicious_glyph_indexes: tuple[int, ...] = ()
    """0-based index(es) of the suspicious glyph(s) among the token's non-space
    glyphs (e.g. ``(2,)`` for the inserted '0' in ``18071.23``)."""

    suspicious_bboxes: tuple[tuple[float, float, float, float], ...] = ()
    """Per-glyph bounding box(es) of the suspicious character(s)."""


@dataclass(frozen=True)
class FontReport:
    """Per-file result of the font-forensics stage (rich, stage-native).

    ``ok`` reports whether the stage RAN, independent of the verdict. ``tier`` /
    ``score`` follow the shared confidence rubric.
    """

    path: str
    ok: bool
    tier: ConfidenceTier
    score: int | None
    page_count: int = 0
    distinct_fonts: tuple[str, ...] = ()
    findings: tuple[FontFinding, ...] = ()
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    error: str | None = None
    raw_size: int = 0
    _lines: tuple[TextLine, ...] = field(default=(), repr=False, compare=False)
    """Grouped lines retained for inspection/tests; excluded from repr/compare."""
