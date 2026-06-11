"""Data models for Stage 1 (revision recovery).

Pure data, no logic. Each pipeline stage is a function over these structures so
later stages (font fingerprinting, OCR cross-check) can plug in without touching
revision recovery.

Only the revision-*detection* models exist so far. Reconstruction, diff,
scoring, and report models will be added with their respective modules (see
../../CLAUDE.md "Module layout").
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
