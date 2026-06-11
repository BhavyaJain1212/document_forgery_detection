"""Revision detection — scan raw PDF bytes for incremental-update structure.

This module is the first step of Stage 1. It operates purely on the raw byte
string (no PDF parser, no third-party deps) and enumerates *candidate* revision
boundaries:

    1. Every ``%%EOF`` marker (a candidate end-of-revision).
    2. Every ``startxref`` keyword + the offset it points at.
    3. Every ``/Prev`` trailer pointer (the incremental-update back-link chain).

For each ``%%EOF`` it produces a :class:`RevisionBoundary` giving the byte length
to truncate to. Detection does **not** load or validate anything — a ``%%EOF``
may legitimately sit inside a stream and is filtered out later, by the
reconstruction step that actually tries to load ``bytes[0:truncate_len]``. So
every marker is reported here; nothing is dropped.

Read-only by construction: :func:`detect_from_path` opens the file ``"rb"`` and
never writes. Malformed/empty/non-incremental inputs return a populated
:class:`DetectionResult` with diagnostic ``notes`` rather than raising.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import DetectionResult, EOFMarker, RevisionBoundary, XrefSection

# ``%%EOF`` is the end-of-file marker that closes each revision (PDF 32000-1, 7.5.5).
_EOF_RE = re.compile(rb"%%EOF")

# ``startxref`` is followed by whitespace then the byte offset of the xref
# table/stream for that revision (PDF 32000-1, 7.5.5).
_STARTXREF_RE = re.compile(rb"startxref\s+(\d+)")

# ``/Prev`` in a trailer dict (or xref-stream dict) points at the previous
# revision's xref — the incremental-update back-link. A name token must be
# separated from the integer by whitespace, so ``\s+`` is required.
_PREV_RE = re.compile(rb"/Prev\s+(\d+)")


def _consume_trailing_eol(raw: bytes, pos: int) -> int:
    """Return ``pos`` advanced past a single trailing line terminator, if any.

    Handles ``\\r\\n``, lone ``\\r``, and lone ``\\n``. A clean revision
    truncation includes the EOL that follows ``%%EOF`` (the next revision's body
    begins after it). Only one terminator is consumed.
    """
    if raw[pos : pos + 2] == b"\r\n":
        return pos + 2
    if pos < len(raw) and raw[pos : pos + 1] in (b"\r", b"\n"):
        return pos + 1
    return pos


def _find_eof_markers(raw: bytes) -> list[EOFMarker]:
    """Locate every ``%%EOF`` occurrence in file order."""
    markers: list[EOFMarker] = []
    for i, m in enumerate(_EOF_RE.finditer(raw)):
        offset = m.start()
        end_offset = _consume_trailing_eol(raw, m.end())
        markers.append(EOFMarker(index=i, offset=offset, end_offset=end_offset))
    return markers


def _find_xref_sections(raw: bytes) -> list[XrefSection]:
    """Locate every ``startxref`` + its pointer, in file order."""
    sections: list[XrefSection] = []
    for m in _STARTXREF_RE.finditer(raw):
        sections.append(
            XrefSection(startxref_offset=m.start(), pointer=int(m.group(1)))
        )
    return sections


def _find_prev_pointers(raw: bytes) -> list[tuple[int, int]]:
    """Locate every ``/Prev`` value as ``(byte_offset, pointer)`` pairs."""
    return [(m.start(), int(m.group(1))) for m in _PREV_RE.finditer(raw)]


def detect(raw: bytes) -> DetectionResult:
    """Scan raw PDF bytes and return candidate revision structure.

    Never raises on content: empty or marker-less inputs come back with an empty
    ``boundaries`` tuple and an explanatory note.

    Each ``%%EOF`` becomes one :class:`RevisionBoundary`. The ``startxref`` and
    ``/Prev`` that fall in the byte range *(previous EOF end, this EOF start)* are
    attached to that boundary as corroborating evidence for reconstruction.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError(f"detect() expects bytes, got {type(raw).__name__}")
    raw = bytes(raw)

    notes: list[str] = []

    if len(raw) == 0:
        return DetectionResult(raw_size=0, notes=("input is empty",))

    if not raw.lstrip()[:5].startswith(b"%PDF-"):
        # Not fatal: a truncated/odd header still gets scanned. Just flag it.
        notes.append("input does not start with a %PDF- header")

    eof_markers = _find_eof_markers(raw)
    xref_sections = _find_xref_sections(raw)
    prev_pairs = _find_prev_pointers(raw)

    if not eof_markers:
        notes.append("no %%EOF marker found; cannot enumerate revisions")
        return DetectionResult(
            raw_size=len(raw),
            xref_sections=tuple(xref_sections),
            prev_pointers=tuple(p for _, p in prev_pairs),
            notes=tuple(notes),
        )

    boundaries = _build_boundaries(eof_markers, xref_sections, prev_pairs)

    if len(boundaries) == 1:
        notes.append("single revision: INCONCLUSIVE for this method (needs later stages)")
    else:
        notes.append(f"{len(boundaries)} candidate revisions found (pre load-validation)")

    return DetectionResult(
        raw_size=len(raw),
        eof_markers=tuple(eof_markers),
        boundaries=tuple(boundaries),
        xref_sections=tuple(xref_sections),
        prev_pointers=tuple(p for _, p in prev_pairs),
        notes=tuple(notes),
    )


def _build_boundaries(
    eof_markers: list[EOFMarker],
    xref_sections: list[XrefSection],
    prev_pairs: list[tuple[int, int]],
) -> list[RevisionBoundary]:
    """Pair each ``%%EOF`` with the ``startxref``/``/Prev`` inside its revision.

    A revision spans *(previous EOF end, this EOF start)*. The relevant
    ``startxref`` and ``/Prev`` are the *last* such tokens before the EOF (a
    revision can rewrite earlier ones; the closing pair wins).
    """
    boundaries: list[RevisionBoundary] = []
    prev_end = 0
    for rev_index, eof in enumerate(eof_markers):
        lo, hi = prev_end, eof.offset

        startxref = next(
            (
                x
                for x in reversed(xref_sections)
                if lo <= x.startxref_offset < hi
            ),
            None,
        )
        prev_pointer = next(
            (ptr for off, ptr in reversed(prev_pairs) if lo <= off < hi),
            None,
        )

        boundaries.append(
            RevisionBoundary(
                index=rev_index,
                eof=eof,
                truncate_len=eof.end_offset,
                startxref=startxref,
                prev_pointer=prev_pointer,
            )
        )
        prev_end = eof.end_offset
    return boundaries


def detect_from_path(path: str | Path) -> DetectionResult:
    """Read a PDF file (read-only) and run :func:`detect` on its bytes.

    The file is opened ``"rb"`` and never modified. A missing/unreadable path is
    reported via ``notes`` (with an empty result) rather than raising, so batch
    callers never crash on one bad file.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return DetectionResult(raw_size=0, notes=(f"file not found: {p}",))
    except IsADirectoryError:
        return DetectionResult(raw_size=0, notes=(f"path is a directory, not a file: {p}",))
    except OSError as exc:
        return DetectionResult(raw_size=0, notes=(f"could not read {p}: {exc}",))

    return detect(raw)
