"""Revision reconstruction — truncate each detected boundary and load it.

Second step of Stage 1. For every *valid* :class:`RevisionBoundary` from
:mod:`detect`, this module truncates ``raw[0:truncate_len]`` and asks pikepdf
(qpdf) to open it. A boundary that opens becomes a :class:`Revision`; one that
does not (corrupt, encrypted-with-password, or a structural false positive that
slips past detection's cheap check) becomes a :class:`ReconstructionFailure` —
reported and skipped, never crashing the run and never silently dropped.

This is the authoritative validity gate: detection's ``startxref``-tail heuristic
says a boundary *could* be a clean tail; here qpdf proves whether it actually is.

The input bytes are never modified; truncation produces new byte slices.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pikepdf

from .detect import detect
from .models import (
    DetectionResult,
    ReconstructionFailure,
    ReconstructionResult,
    Revision,
)


def _load_revision(data: bytes, *, index: int, boundary_index: int) -> Revision:
    """Open ``data`` with pikepdf and capture revision metadata.

    Raises whatever pikepdf raises; the caller turns failures into
    :class:`ReconstructionFailure` entries.
    """
    # No password supplied: an empty-user-password encrypted PDF still opens
    # (is_encrypted becomes True); one that needs a real password raises
    # PasswordError, which the caller records as a failure.
    with pikepdf.open(BytesIO(data)) as pdf:
        page_count = len(pdf.pages)
        is_encrypted = bool(pdf.is_encrypted)
    return Revision(
        index=index,
        source_boundary_index=boundary_index,
        truncate_len=len(data),
        page_count=page_count,
        is_encrypted=is_encrypted,
        data=data,
    )


def reconstruct(raw: bytes, detection: DetectionResult) -> ReconstructionResult:
    """Reconstruct and load every valid boundary from ``detection``.

    Returns successfully loaded :class:`Revision` objects (earliest first,
    re-indexed from 0) plus a :class:`ReconstructionFailure` for each valid
    boundary that would not load. Only structurally *valid* boundaries are
    attempted; invalid (in-stream) ``%%EOF`` candidates were already flagged at
    detection time.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError(f"reconstruct() expects bytes, got {type(raw).__name__}")
    raw = bytes(raw)

    revisions: list[Revision] = []
    failures: list[ReconstructionFailure] = []
    notes: list[str] = []

    valid_boundaries = detection.valid_boundaries
    if not valid_boundaries:
        notes.append("no valid revision boundary to reconstruct")
        return ReconstructionResult(notes=tuple(notes))

    for boundary in valid_boundaries:
        data = raw[: boundary.truncate_len]
        try:
            revision = _load_revision(
                data, index=len(revisions), boundary_index=boundary.index
            )
        except pikepdf.PasswordError:
            failures.append(
                ReconstructionFailure(
                    source_boundary_index=boundary.index,
                    truncate_len=boundary.truncate_len,
                    reason="encrypted: password required (cannot reconstruct)",
                )
            )
            continue
        except pikepdf.PdfError as exc:
            failures.append(
                ReconstructionFailure(
                    source_boundary_index=boundary.index,
                    truncate_len=boundary.truncate_len,
                    reason=f"unloadable: {exc}",
                )
            )
            continue
        except Exception as exc:  # never crash the run on one bad boundary
            failures.append(
                ReconstructionFailure(
                    source_boundary_index=boundary.index,
                    truncate_len=boundary.truncate_len,
                    reason=f"unexpected error ({type(exc).__name__}): {exc}",
                )
            )
            continue

        revisions.append(revision)

    notes.append(
        f"reconstructed {len(revisions)} of {len(valid_boundaries)} valid boundaries"
    )
    if failures:
        notes.append(
            f"{len(failures)} detected revision(s) could not be reconstructed "
            "(reported for scoring, not dropped)"
        )
    if len(revisions) == 1:
        notes.append("single reconstructed revision: INCONCLUSIVE (needs later stages)")

    return ReconstructionResult(
        revisions=tuple(revisions),
        failures=tuple(failures),
        notes=tuple(notes),
    )


def reconstruct_from_path(path: str | Path) -> ReconstructionResult:
    """Read a PDF (read-only), detect boundaries, and reconstruct revisions.

    A missing/unreadable path is reported via ``notes`` (empty result) rather
    than raised, so batch callers never crash on one bad file.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return ReconstructionResult(notes=(f"file not found: {p}",))
    except IsADirectoryError:
        return ReconstructionResult(notes=(f"path is a directory, not a file: {p}",))
    except OSError as exc:
        return ReconstructionResult(notes=(f"could not read {p}: {exc}",))

    return reconstruct(raw, detect(raw))
