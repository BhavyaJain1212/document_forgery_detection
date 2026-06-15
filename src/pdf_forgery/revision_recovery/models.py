"""Data models for Stage 1 (revision recovery).

Pure data, no logic. Each pipeline stage is a function over these structures so
later stages (font fingerprinting, OCR cross-check) can plug in without touching
revision recovery.

Scoring and report models will be added with their respective modules (see
../../CLAUDE.md "Module layout").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class EOFMarker:
    """A single ``%%EOF`` occurrence in the raw bytes.

    A ``%%EOF`` marker is a *candidate* end-of-revision. Some markers legitimately
    sit inside stream data and are not real boundaries; detection reports every
    occurrence and leaves load-validation to reconstruction.
    """

    index: int
    """0-based order of this marker in the file (earliest = 0)."""

    offset: int
    """Byte offset where the literal ``%%EOF`` begins."""

    end_offset: int
    """Byte offset just past ``%%EOF`` and any single trailing EOL.

    ``raw_bytes[0:end_offset]`` is the candidate truncation for this revision.
    """


@dataclass(frozen=True)
class XrefSection:
    """A ``startxref`` keyword and the byte offset it points at."""

    startxref_offset: int
    """Byte offset of the ``startxref`` keyword."""

    pointer: int
    """The integer following ``startxref`` (offset of the xref table/stream)."""


@dataclass(frozen=True)
class RevisionBoundary:
    """One candidate historical revision, earliest first.

    ``raw_bytes[0:truncate_len]`` is the reconstructed revision. The corroborating
    ``startxref``/``/Prev`` pointers are recorded for cross-checking and for the
    reconstruction step; they are advisory at detection time.
    """

    index: int
    """0-based revision index; 0 is the earliest recoverable revision."""

    eof: EOFMarker
    """The ``%%EOF`` marker that closes this revision."""

    truncate_len: int
    """Length to truncate the raw bytes to for this revision (== ``eof.end_offset``)."""

    startxref: XrefSection | None = None
    """The ``startxref`` belonging to this revision, if one was found before its EOF."""

    prev_pointer: int | None = None
    """The ``/Prev`` value in this revision's trailer, if present (points at the
    previous revision's xref). Absent on the earliest revision."""

    valid: bool = True
    """Whether this ``%%EOF`` is a structurally clean revision boundary.

    A *cheap* structural check (no PDF loading): a real boundary is immediately
    preceded by ``startxref <offset>``. A ``%%EOF`` that sits inside stream data
    has no such tail and is flagged ``valid=False`` instead of being dropped.
    The authoritative load-test happens later in reconstruction.
    """

    invalid_reason: str | None = None
    """Why this candidate was flagged ``valid=False`` (``None`` when valid)."""


@dataclass(frozen=True)
class DetectionResult:
    """Outcome of scanning one PDF's raw bytes for revision structure."""

    raw_size: int
    """Total size of the input in bytes."""

    eof_markers: tuple[EOFMarker, ...] = ()
    """Every ``%%EOF`` occurrence found, in file order."""

    boundaries: tuple[RevisionBoundary, ...] = ()
    """ALL candidate boundaries, earliest -> latest, equal in count to
    ``eof_markers``. Includes structurally-invalid candidates (e.g. in-stream
    ``%%EOF``) flagged ``valid=False`` — they are never silently dropped."""

    xref_sections: tuple[XrefSection, ...] = ()
    """Every ``startxref`` found, in file order."""

    prev_pointers: tuple[int, ...] = ()
    """Every ``/Prev`` value found, in file order (corroborates the revision chain)."""

    notes: tuple[str, ...] = field(default_factory=tuple)
    """Human-readable diagnostics (e.g. 'no %%EOF found'). Never raises; reports."""

    @property
    def valid_boundaries(self) -> tuple[RevisionBoundary, ...]:
        """Only the structurally clean candidate boundaries."""
        return tuple(b for b in self.boundaries if b.valid)

    @property
    def candidate_count(self) -> int:
        """Total ``%%EOF`` candidates found, valid or not."""
        return len(self.boundaries)

    @property
    def revision_count(self) -> int:
        """Number of structurally valid revisions (pre load-validation)."""
        return len(self.valid_boundaries)

    @property
    def is_multi_revision(self) -> bool:
        """True if more than one *valid* revision boundary was found."""
        return self.revision_count > 1


@dataclass(frozen=True)
class Revision:
    """A historical revision recovered by truncating + load-validating the bytes.

    ``data`` is exactly ``raw[0:truncate_len]`` and is itself a complete, loadable
    PDF (qpdf opened it). Downstream stages re-open ``data`` to extract text and
    inspect objects, so each revision is self-contained.
    """

    index: int
    """0-based position among *successfully reconstructed* revisions (earliest = 0)."""

    source_boundary_index: int
    """Index of the originating :class:`RevisionBoundary` in the detection result."""

    truncate_len: int
    """Byte length this revision was truncated to (``len(data)``)."""

    page_count: int
    """Number of pages qpdf reported for this revision."""

    is_encrypted: bool
    """True if the revision is encrypted but opened with an empty password."""

    data: bytes = field(repr=False)
    """The reconstructed revision bytes (``raw[0:truncate_len]``). A complete PDF.
    Excluded from ``repr`` to avoid dumping the whole file."""


@dataclass(frozen=True)
class ReconstructionFailure:
    """A valid boundary that could not be loaded as a PDF (reported, not dropped).

    Feeds the scoring rubric's MEDIUM rule: "a revision was detected but could not
    be reconstructed/extracted (corruption or evasion -- never silently drop it)."
    """

    source_boundary_index: int
    """Index of the originating :class:`RevisionBoundary` in the detection result."""

    truncate_len: int
    """Byte length the failed truncation would have had."""

    reason: str
    """Why the load failed (e.g. 'encrypted: password required', 'unloadable: ...')."""


@dataclass(frozen=True)
class ReconstructionResult:
    """Outcome of reconstructing revisions from one PDF's detected boundaries."""

    revisions: tuple[Revision, ...] = ()
    """Successfully loaded revisions, earliest -> latest, re-indexed from 0."""

    failures: tuple[ReconstructionFailure, ...] = ()
    """Valid boundaries that would not load. Surfaced for scoring; never dropped."""

    notes: tuple[str, ...] = field(default_factory=tuple)
    """Human-readable diagnostics. Never raises; reports."""

    @property
    def revision_count(self) -> int:
        """Number of successfully reconstructed revisions."""
        return len(self.revisions)

    @property
    def has_failures(self) -> bool:
        """True if any detected revision could not be reconstructed."""
        return len(self.failures) > 0

    @property
    def is_multi_revision(self) -> bool:
        """True if more than one revision was successfully reconstructed."""
        return self.revision_count > 1


# ---------------------------------------------------------------------------
# Text-diff models (Task 4)
# ---------------------------------------------------------------------------

class HighValueKind(str, Enum):
    """Category of a high-value token match.

    ``str`` mixin lets the value serialize to plain JSON without extra work.
    Priority for choosing among multiple matches: AMOUNT > DATE > ID_LIKE.
    """

    AMOUNT = "amount"
    DATE = "date"
    ID_LIKE = "id_like"


@dataclass(frozen=True)
class CharSpan:
    """One segment of a character-level diff inside a changed token.

    ``tag`` mirrors difflib opcode tags: ``"equal"``, ``"replace"``,
    ``"insert"``, ``"delete"``.
    """

    tag: str
    before: str
    after: str


@dataclass(frozen=True)
class TokenDiff:
    """Before/after for a single changed token, with character-level detail.

    ``before`` is empty when the token was purely inserted; ``after`` is empty
    when it was purely deleted.  ``high_value`` is ``None`` when neither side
    matches a high-value pattern.
    """

    before: str
    after: str
    char_diff: tuple[CharSpan, ...] = ()
    high_value: HighValueKind | None = None


@dataclass(frozen=True)
class PageTextDiff:
    """Text-layer diff for one page between two consecutive revisions."""

    page_index: int
    before_text: str
    """Normalized full-page text from the earlier revision."""

    after_text: str
    """Normalized full-page text from the later revision."""

    token_changes: tuple[TokenDiff, ...]
    is_substantive: bool
    """True if >= 1 token was added, removed, or changed after normalization."""

    has_high_value_change: bool
    """True if any changed token matched a high-value pattern."""


@dataclass(frozen=True)
class TextChange:
    """All page-level text changes between two consecutive revisions.

    The primary output of ``diff.textdiff``. Consumed by ``scoring`` and
    ``report``.
    """

    from_revision: int
    to_revision: int
    page_diffs: tuple[PageTextDiff, ...]
    is_substantive: bool
    """True if any page has a substantive text change."""

    has_high_value_change: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Object-diff models (Task 4b)
# ---------------------------------------------------------------------------

class ObjectChangeClass(str, Enum):
    """Classification of a changed PDF object between consecutive revisions.

    ``str`` mixin lets values serialize to plain JSON without extra work.
    """

    CONTENT = "content"
    SIGNATURE = "signature"
    MARKUP = "markup"
    OVERLAY = "overlay"
    FORM_FILL = "form_fill"
    FIELD_EDIT = "field_edit"
    META = "meta"


@dataclass(frozen=True)
class ObjectChange:
    """One changed or new PDF object and its forgery-relevant classification."""

    obj_num: int
    gen_num: int
    change_class: ObjectChangeClass
    page_index: int | None
    """0-based page index, if determinable from the PDF structure."""

    is_new: bool
    """True if the object is absent from the earlier revision (newly added)."""

    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObjectDiff:
    """All object-level changes between two consecutive revisions."""

    from_revision: int
    to_revision: int
    changes: tuple[ObjectChange, ...]
    notes: tuple[str, ...] = ()

    @property
    def by_class(self) -> dict[ObjectChangeClass, list[ObjectChange]]:
        """Changes grouped by classification."""
        result: dict[ObjectChangeClass, list[ObjectChange]] = {}
        for c in self.changes:
            result.setdefault(c.change_class, []).append(c)
        return result


# ---------------------------------------------------------------------------
# Scoring models (Task 5)
# ---------------------------------------------------------------------------


# ``ConfidenceTier`` now lives in the stage-agnostic core so every detection
# stage shares one definition (INCONCLUSIVE / LOW / MEDIUM / HIGH with identical
# string values). It is re-exported here so existing
# ``revision_recovery.models.ConfidenceTier`` imports keep working unchanged.
from ..core.types import ConfidenceTier  # noqa: E402  (re-export)


@dataclass(frozen=True)
class ScoringResult:
    """Output of the scoring rule tree for one PDF analysis.

    The ``score`` is advisory; a human reviewer decides.  ``reasons`` gives
    a human-readable explanation of the tier decision.  ``report.py`` combines
    this with the raw :class:`TextChange` / :class:`ObjectDiff` data to render
    the before→after evidence.
    """

    tier: ConfidenceTier

    score: int | None
    """Numeric score within the tier's band, or ``None`` for INCONCLUSIVE."""

    reasons: tuple[str, ...]
    """Ordered explanation of the scoring decision (most significant first)."""

    object_classes_seen: tuple[ObjectChangeClass, ...]
    """All change classes found across every consecutive revision pair."""

    has_substantive_text_change: bool
    """True if any page in any pair had a substantive normalized text diff."""

    has_high_value_change: bool
    """True if any changed token matched a high-value pattern (before Config toggles)."""

    high_value_kind: HighValueKind | None
    """Highest-priority HighValueKind that drove the score, after Config toggles.
    ``None`` when no high-value pattern was active or the toggles suppressed it."""

    revision_count: int
    """Number of revisions successfully reconstructed."""

    has_reconstruction_failures: bool
    """True if any detected revision could not be reconstructed."""

    notes: tuple[str, ...]
    """Diagnostics / warnings that do not affect the tier decision."""


# ---------------------------------------------------------------------------
# Report models (Task 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One flagged change between two consecutive revisions, with evidence.

    A finding bundles exactly what the spec requires for every flagged change:
    the revision indices, the changed object id(s) and their classification, the
    page number, and the exact before -> after text.  Findings are advisory; a
    human reviewer makes the final call.
    """

    from_revision: int
    """Index of the earlier revision in the compared pair."""

    to_revision: int
    """Index of the later revision in the compared pair."""

    page_index: int | None
    """0-based page number the change appears on, or ``None`` if not page-bound."""

    object_ids: tuple[str, ...]
    """Changed object ids as ``"<obj> <gen>"`` strings (e.g. ``"12 0"``)."""

    object_classes: tuple[ObjectChangeClass, ...]
    """Classifications of the changed objects backing this finding."""

    token_changes: tuple[TokenDiff, ...]
    """Per-token before/after detail for the text change (empty for object-only)."""

    is_high_value: bool
    """True if any changed token matched a high-value pattern (amount/date/id)."""

    high_value_kind: HighValueKind | None
    """Highest-priority high-value kind among the changed tokens, if any."""

    summary: str
    """One-line human description of the finding."""

    @property
    def before_text(self) -> str:
        """Concatenated before-text of the changed tokens (space-joined)."""
        return " ".join(tc.before for tc in self.token_changes if tc.before)

    @property
    def after_text(self) -> str:
        """Concatenated after-text of the changed tokens (space-joined)."""
        return " ".join(tc.after for tc in self.token_changes if tc.after)


@dataclass(frozen=True)
class AnalysisReport:
    """Top-level result of analysing one PDF file.

    Produced by ``analyze_path`` / ``analyze_bytes`` and rendered to JSON or a
    human summary by ``report.py``.  ``ok`` reports whether the *run* succeeded,
    independent of the verdict — a clean single-revision PDF is ``ok=True`` with
    an INCONCLUSIVE tier.  A file that could not be read is ``ok=False`` with an
    ``error`` and no scoring.
    """

    path: str
    """Filesystem path of the analysed input."""

    ok: bool
    """True if the analysis ran to completion (NOT a verdict)."""

    error: str | None
    """Why the run failed (e.g. 'file not found'); ``None`` when ``ok``."""

    raw_size: int
    """Size of the input in bytes (0 when unreadable)."""

    candidate_count: int
    """Total ``%%EOF`` candidate boundaries detected (valid or not)."""

    revision_count: int
    """Number of revisions successfully reconstructed."""

    reconstruction_failures: int
    """Number of detected revisions that could not be reconstructed."""

    scoring: ScoringResult | None
    """The scoring result, or ``None`` when the run failed before scoring."""

    findings: tuple[Finding, ...]
    """Every flagged change with before -> after evidence."""

    text_changes: tuple[TextChange, ...]
    """Full text-diff detail per consecutive revision pair (for the JSON output)."""

    object_diffs: tuple[ObjectDiff, ...]
    """Full object-diff detail per consecutive revision pair (for the JSON output)."""

    notes: tuple[str, ...]
    """Aggregated diagnostics from detection, reconstruction, and diffing."""
