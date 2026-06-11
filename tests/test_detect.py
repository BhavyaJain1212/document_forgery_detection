"""Tests for revision detection (raw-byte scanning).

These are deliberately dependency-free: detection works on the raw byte string,
so the fixtures here are hand-crafted bytes, not real PDFs. Load-validation of
boundaries is the reconstruction step's job and is tested separately.
"""

from __future__ import annotations

import pytest

from pdf_forgery.revision_recovery import detect, detect_from_path
from pdf_forgery.revision_recovery.models import DetectionResult


# --------------------------------------------------------------------------- #
# Byte fixtures
# --------------------------------------------------------------------------- #

def _single_revision() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"xref\n0 3\n"
        b"0000000000 65535 f \n"
        b"trailer\n<< /Size 3 /Root 1 0 R >>\n"
        b"startxref\n9\n"
        b"%%EOF\n"
    )


def _two_revisions() -> bytes:
    """A single-revision body followed by an incremental update with /Prev."""
    rev1 = _single_revision()
    rev2 = (
        b"3 0 obj\n<< /Type /Page /Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Length 20 >>\nstream\n(claim amount edited)\nendstream\nendobj\n"
        b"xref\n0 1\n0000000000 65535 f \n"
        b"trailer\n<< /Size 5 /Root 1 0 R /Prev 9 >>\n"
        b"startxref\n312\n"
        b"%%EOF\n"
    )
    return rev1 + rev2


def _eof_inside_stream() -> bytes:
    """One real revision, but a stray %%EOF lives inside stream data.

    Detection must report BOTH markers (it never decides on its own which are
    real); reconstruction's load-validation filters the bogus one.
    """
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Length 12 >>\nstream\nhi %%EOF\n bye\nendstream\nendobj\n"
        b"xref\n0 2\n0000000000 65535 f \n"
        b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
        b"startxref\n9\n"
        b"%%EOF\n"
    )


# --------------------------------------------------------------------------- #
# Core behaviour
# --------------------------------------------------------------------------- #

def test_single_revision_yields_one_boundary():
    raw = _single_revision()
    result = detect(raw)

    assert isinstance(result, DetectionResult)
    assert result.revision_count == 1
    assert result.is_multi_revision is False
    assert result.raw_size == len(raw)
    assert any("single revision" in n.lower() for n in result.notes)


def test_two_revisions_yields_two_boundaries_in_order():
    raw = _two_revisions()
    result = detect(raw)

    assert result.revision_count == 2
    assert result.is_multi_revision is True
    # earliest first
    assert [b.index for b in result.boundaries] == [0, 1]
    assert result.boundaries[0].truncate_len < result.boundaries[1].truncate_len


def test_truncate_len_reconstructs_first_revision_exactly():
    raw = _two_revisions()
    first = detect(raw).boundaries[0]
    # bytes[0:truncate_len] is exactly the first revision (incl. its EOF + EOL).
    assert raw[: first.truncate_len] == _single_revision()
    assert raw[first.truncate_len - len(b"%%EOF\n") :][:5] == b"%%EOF"


def test_prev_pointer_attached_to_second_revision_only():
    result = detect(_two_revisions())
    assert result.boundaries[0].prev_pointer is None
    assert result.boundaries[1].prev_pointer == 9
    assert result.prev_pointers == (9,)


def test_startxref_attached_per_revision():
    result = detect(_two_revisions())
    assert result.boundaries[0].startxref is not None
    assert result.boundaries[0].startxref.pointer == 9
    assert result.boundaries[1].startxref is not None
    assert result.boundaries[1].startxref.pointer == 312
    assert tuple(x.pointer for x in result.xref_sections) == (9, 312)


def test_eof_inside_stream_is_flagged_invalid_not_dropped():
    # Both %%EOF markers are reported (never silently dropped), but the in-stream
    # one is flagged invalid so it is not counted as a real revision.
    result = detect(_eof_inside_stream())

    assert result.candidate_count == 2  # nothing dropped
    assert result.revision_count == 1  # only the real boundary counts
    assert result.is_multi_revision is False

    in_stream, real = result.boundaries
    assert in_stream.valid is False
    assert in_stream.invalid_reason and "stream" in in_stream.invalid_reason
    assert real.valid is True
    assert real.invalid_reason is None

    # valid_boundaries exposes only the clean one
    assert result.valid_boundaries == (real,)
    assert any("invalid" in n.lower() for n in result.notes)


def test_clean_revisions_are_all_valid():
    result = detect(_two_revisions())
    assert result.candidate_count == 2
    assert [b.valid for b in result.boundaries] == [True, True]
    assert all(b.invalid_reason is None for b in result.boundaries)


# --------------------------------------------------------------------------- #
# EOL handling
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("eol", [b"\n", b"\r\n", b"\r", b""])
def test_trailing_eol_consumed_once(eol):
    raw = b"%PDF-1.4\nbody\nstartxref\n9\n%%EOF" + eol
    result = detect(raw)
    assert result.revision_count == 1
    # truncate_len covers %%EOF plus the single EOL, i.e. the whole input here.
    assert result.boundaries[0].truncate_len == len(raw)


# --------------------------------------------------------------------------- #
# Graceful degradation (never raise on content)
# --------------------------------------------------------------------------- #

def test_empty_input():
    result = detect(b"")
    assert result.revision_count == 0
    assert result.raw_size == 0
    assert any("empty" in n.lower() for n in result.notes)


def test_no_eof_marker():
    result = detect(b"%PDF-1.4\nsome body with no end marker\n")
    assert result.revision_count == 0
    assert any("no %%eof" in n.lower() for n in result.notes)


def test_missing_pdf_header_is_noted_not_fatal():
    raw = b"garbage\nstartxref\n9\n%%EOF\n"
    result = detect(raw)
    assert result.revision_count == 1  # still scanned
    assert any("header" in n.lower() for n in result.notes)


def test_non_bytes_input_raises_typeerror():
    with pytest.raises(TypeError):
        detect("i am a str")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# detect_from_path: read-only, crash-free
# --------------------------------------------------------------------------- #

def test_detect_from_path_reads_and_does_not_modify(tmp_path):
    p = tmp_path / "doc.pdf"
    raw = _two_revisions()
    p.write_bytes(raw)
    before = p.read_bytes()
    mtime_before = p.stat().st_mtime_ns

    result = detect_from_path(p)

    assert result.revision_count == 2
    assert p.read_bytes() == before  # unchanged
    assert p.stat().st_mtime_ns == mtime_before  # not even touched


def test_detect_from_path_missing_file_is_reported_not_raised(tmp_path):
    result = detect_from_path(tmp_path / "nope.pdf")
    assert result.revision_count == 0
    assert any("not found" in n.lower() for n in result.notes)


def test_detect_from_path_directory_is_reported_not_raised(tmp_path):
    result = detect_from_path(tmp_path)
    assert result.revision_count == 0
    assert any("director" in n.lower() for n in result.notes)
