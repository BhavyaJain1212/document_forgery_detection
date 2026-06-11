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


@dataclass(frozen=True)
class DetectionResult:
    """Outcome of scanning one PDF's raw bytes for revision structure."""

    raw_size: int
    """Total size of the input in bytes."""

    eof_markers: tuple[EOFMarker, ...] = ()
    """Every ``%%EOF`` occurrence found, in file order."""

    boundaries: tuple[RevisionBoundary, ...] = ()
    """Candidate revisions, earliest -> latest. Equal in count to ``eof_markers``."""

    xref_sections: tuple[XrefSection, ...] = ()
    """Every ``startxref`` found, in file order."""

    prev_pointers: tuple[int, ...] = ()
    """Every ``/Prev`` value found, in file order (corroborates the revision chain)."""

    notes: tuple[str, ...] = field(default_factory=tuple)
    """Human-readable diagnostics (e.g. 'no %%EOF found'). Never raises; reports."""

    @property
    def revision_count(self) -> int:
        """Number of candidate revisions (pre load-validation)."""
        return len(self.boundaries)

    @property
    def is_multi_revision(self) -> bool:
        """True if more than one candidate revision boundary was found."""
        return len(self.boundaries) > 1
