"""Tests for revision reconstruction (truncate + pikepdf load-validate).

Unlike detection (pure byte scanning), reconstruction needs *real* loadable
PDFs, so these fixtures are built with pikepdf. ``_append_incremental`` performs
a genuine incremental update: it appends a new object + xref + trailer (with
``/Prev``) + ``startxref`` + ``%%EOF`` after a base PDF, leaving the original
bytes untouched — exactly the "Save, not Save As" pattern this stage detects.
"""

from __future__ import annotations

import io

import pikepdf
import pytest

from pdf_forgery.revision_recovery import detect, reconstruct, reconstruct_from_path
from pdf_forgery.revision_recovery.models import (
    DetectionResult,
    EOFMarker,
    ReconstructionResult,
    RevisionBoundary,
)


# --------------------------------------------------------------------------- #
# Real-PDF fixtures
# --------------------------------------------------------------------------- #

def _base_pdf() -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(300, 300))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _append_incremental(base: bytes) -> bytes:
    """Append a genuine incremental-update revision to ``base``."""
    prev_sx = detect(base).valid_boundaries[-1].startxref.pointer
    with pikepdf.open(io.BytesIO(base)) as p:
        old_size = int(p.trailer.Size)
        root_num, root_gen = p.Root.objgen

    new_num = old_size
    obj = f"{new_num} 0 obj\n<< /RevisionMarker (rev2 edit) >>\nendobj\n".encode()
    obj_offset = len(base)
    xref_offset = obj_offset + len(obj)
    xref = (
        b"xref\n"
        + f"{new_num} 1\n".encode()
        + f"{obj_offset:010d} 00000 n \n".encode()
        + b"trailer\n"
        + f"<< /Size {new_num + 1} /Root {root_num} {root_gen} R /Prev {prev_sx} >>\n".encode()
        + b"startxref\n"
        + f"{xref_offset}\n".encode()
        + b"%%EOF\n"
    )
    return base + obj + xref


def _encrypted_pdf(*, user: str, owner: str = "owner") -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(owner=owner, user=user))
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

def test_single_revision_reconstructs():
    raw = _base_pdf()
    result = reconstruct(raw, detect(raw))

    assert isinstance(result, ReconstructionResult)
    assert result.revision_count == 1
    assert result.is_multi_revision is False
    assert result.has_failures is False
    assert result.revisions[0].page_count == 1
    assert any("single reconstructed revision" in n.lower() for n in result.notes)


def test_two_revisions_reconstruct_in_order():
    raw = _append_incremental(_base_pdf())
    result = reconstruct(raw, detect(raw))

    assert result.revision_count == 2
    assert result.is_multi_revision is True
    assert not result.has_failures
    assert [r.index for r in result.revisions] == [0, 1]
    assert [r.source_boundary_index for r in result.revisions] == [0, 1]
    # earlier revision is strictly shorter than the later (full) one
    assert result.revisions[0].truncate_len < result.revisions[1].truncate_len


def test_revision_data_is_exact_truncation_and_independently_loadable():
    raw = _append_incremental(_base_pdf())
    result = reconstruct(raw, detect(raw))

    for rev in result.revisions:
        assert rev.data == raw[: rev.truncate_len]
        # each reconstructed revision is itself a complete, loadable PDF
        with pikepdf.open(io.BytesIO(rev.data)) as pdf:
            assert len(pdf.pages) == rev.page_count


# --------------------------------------------------------------------------- #
# Graceful failure handling (report + skip, never crash)
# --------------------------------------------------------------------------- #

def test_unloadable_valid_boundary_becomes_failure():
    # Passes detection's startxref-tail heuristic, but qpdf cannot open it.
    raw = b"%PDF-1.4\ntotally broken not a pdf\nstartxref\n5\n%%EOF\n"
    detection = detect(raw)
    assert detection.revision_count == 1  # looked valid structurally

    result = reconstruct(raw, detection)
    assert result.revision_count == 0
    assert result.has_failures
    assert "unloadable" in result.failures[0].reason
    assert result.failures[0].source_boundary_index == 0
    assert any("could not be reconstructed" in n.lower() for n in result.notes)


def test_encrypted_password_required_is_failure_not_crash():
    raw = _encrypted_pdf(user="secret")
    result = reconstruct(raw, detect(raw))

    assert result.revision_count == 0
    assert result.has_failures
    assert "encrypted" in result.failures[0].reason.lower()


def test_encrypted_empty_user_password_loads_and_flags_encrypted():
    raw = _encrypted_pdf(user="")
    result = reconstruct(raw, detect(raw))

    assert result.revision_count == 1
    assert result.revisions[0].is_encrypted is True


def test_no_valid_boundaries_returns_empty_with_note():
    result = reconstruct(b"", detect(b""))
    assert result.revision_count == 0
    assert not result.has_failures
    assert any("no valid revision boundary" in n.lower() for n in result.notes)


def test_invalid_boundaries_are_not_attempted():
    # Detection-flagged-invalid boundaries must be skipped, not turned into
    # failures (they were never candidate revisions).
    base = _base_pdf()
    real = detect(base).boundaries[0]
    bogus = RevisionBoundary(
        index=1,
        eof=EOFMarker(index=1, offset=len(base) + 4, end_offset=len(base) + 10),
        truncate_len=len(base) + 10,
        valid=False,
        invalid_reason="in-stream",
    )
    detection = DetectionResult(raw_size=len(base), boundaries=(real, bogus))

    result = reconstruct(base, detection)
    assert result.revision_count == 1
    assert not result.has_failures


def test_non_bytes_input_raises_typeerror():
    with pytest.raises(TypeError):
        reconstruct("not bytes", detect(b""))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# reconstruct_from_path: read-only, crash-free
# --------------------------------------------------------------------------- #

def test_reconstruct_from_path_reads_and_does_not_modify(tmp_path):
    raw = _append_incremental(_base_pdf())
    p = tmp_path / "doc.pdf"
    p.write_bytes(raw)
    before = p.read_bytes()
    mtime_before = p.stat().st_mtime_ns

    result = reconstruct_from_path(p)

    assert result.revision_count == 2
    assert p.read_bytes() == before
    assert p.stat().st_mtime_ns == mtime_before


def test_reconstruct_from_path_missing_file_is_reported_not_raised(tmp_path):
    result = reconstruct_from_path(tmp_path / "nope.pdf")
    assert result.revision_count == 0
    assert any("not found" in n.lower() for n in result.notes)


def test_reconstruct_from_path_directory_is_reported_not_raised(tmp_path):
    result = reconstruct_from_path(tmp_path)
    assert result.revision_count == 0
    assert any("director" in n.lower() for n in result.notes)
