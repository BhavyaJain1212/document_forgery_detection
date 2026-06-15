"""Unit tests for ``objects_written_in_increment`` (the xref-driven changed set).

The helper reads the cross-reference records a revision physically authored in
its own appended byte range ``raw[start:end)`` and must return exactly the
object ids written there — never the phantom "new" objects a truncated-revision
enumeration diff would invent. Three increment shapes are covered:

* a classic ``xref`` table increment,
* a cross-reference *stream* increment, and
* the hybrid / compatibility append (empty ``0 0`` table + a back-pointing
  ``/XRefStm``) that writes zero objects.
"""

from __future__ import annotations

from io import BytesIO

import pikepdf
import pytest

from pdf_forgery.revision_recovery.diff.objectdiff import objects_written_in_increment


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #

def _base_pdf() -> bytes:
    """A small single-page PDF saved by pikepdf (qpdf), used as revision 0."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[-1]
    page.Contents = pdf.make_indirect(
        pikepdf.Stream(pdf, b"BT /F1 12 Tf 72 720 Td (hello) Tj ET\n")
    )
    buf = BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _prev_startxref(base: bytes) -> int:
    from pdf_forgery.revision_recovery.detect import detect

    return detect(base).valid_boundaries[-1].startxref.pointer


def _content_objnum(base: bytes) -> int:
    with pikepdf.open(BytesIO(base)) as p:
        return p.pages[0].Contents.objgen[0]


def _append_classic_increment(base: bytes, objnum: int) -> tuple[bytes, int, int]:
    """Override ``objnum`` with a classic-xref incremental update.

    Returns (full_bytes, start, end) where ``[start, end)`` is the increment.
    """
    stream = b"BT /F1 12 Tf 72 720 Td (edited) Tj ET\n"
    body = (
        f"{objnum} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
        + stream
        + b"\nendstream\nendobj\n"
    )
    with pikepdf.open(BytesIO(base)) as p:
        size = int(p.trailer.Size)
        root = p.Root.objgen

    out = bytearray(base)
    if not out.endswith(b"\n"):
        out += b"\n"
    start = len(out)
    obj_off = len(out)
    out += body
    xref_off = len(out)
    out += b"xref\n"
    out += f"{objnum} 1\n".encode("latin-1")
    out += f"{obj_off:010d} 00000 n \n".encode("latin-1")
    out += b"trailer\n"
    out += (
        f"<< /Size {size} /Root {root[0]} {root[1]} R /Prev {_prev_startxref(base)} >>\n"
    ).encode("latin-1")
    out += b"startxref\n" + f"{xref_off}\n".encode("latin-1") + b"%%EOF\n"
    return bytes(out), start, len(out)


def _append_compatibility_xref(base: bytes) -> tuple[bytes, int, int]:
    """Append a 184-byte-style hybrid/compatibility xref that writes nothing.

    An empty ``0 0`` classic subsection plus a trailer whose ``/XRefStm`` points
    *back* into the earlier revision — exactly the Microsoft hybrid-reference
    case. No object records are authored in the increment.
    """
    with pikepdf.open(BytesIO(base)) as p:
        size = int(p.trailer.Size)
        root = p.Root.objgen
    prev = _prev_startxref(base)

    out = bytearray(base)
    if not out.endswith(b"\n"):
        out += b"\n"
    start = len(out)
    xref_off = len(out)
    out += b"xref\r\n0 0\r\ntrailer\r\n"
    out += (
        f"<< /Size {size} /Root {root[0]} {root[1]} R /Prev {prev} /XRefStm {prev} >>\r\n"
    ).encode("latin-1")
    out += b"startxref\r\n" + f"{xref_off}\r\n".encode("latin-1") + b"%%EOF"
    return bytes(out), start, len(out)


# --------------------------------------------------------------------------- #
# Classic-table increment
# --------------------------------------------------------------------------- #

def test_classic_table_increment_reports_written_object():
    base = _base_pdf()
    cnum = _content_objnum(base)
    full, start, end = _append_classic_increment(base, cnum)
    written = objects_written_in_increment(full, start, end)
    assert written == {(cnum, 0)}


def test_classic_table_increment_excludes_base_objects():
    """Only the one overridden object is reported, not the whole base."""
    base = _base_pdf()
    cnum = _content_objnum(base)
    full, start, end = _append_classic_increment(base, cnum)
    written = objects_written_in_increment(full, start, end)
    # The base has several objects; the increment authored exactly one record.
    assert len(written) == 1


# --------------------------------------------------------------------------- #
# Cross-reference-stream increment (real sample uses xref streams)
# --------------------------------------------------------------------------- #

def test_xref_stream_increment_reports_tamper_objects():
    """The Acrobat sample's tamper revision is an xref-stream increment."""
    from pdf_forgery.revision_recovery.reconstruct import reconstruct_from_path

    import os

    sample = os.path.join("test_pdf's", "Acrobat_Demo_File.pdf")
    if not os.path.exists(sample):
        pytest.skip("Acrobat_Demo_File.pdf sample not present")

    rec = reconstruct_from_path(sample)
    assert rec.revision_count == 2
    rev_a, rev_b = rec.revisions
    written = objects_written_in_increment(
        rev_b.data, len(rev_a.data), len(rev_b.data)
    )
    # The increment is a cross-reference stream listing the rewritten objects.
    assert len(written) > 1
    # Generation numbers are well-formed tuples.
    assert all(isinstance(n, int) and isinstance(g, int) for n, g in written)


# --------------------------------------------------------------------------- #
# Hybrid / compatibility append -> zero writes (the false-positive fix)
# --------------------------------------------------------------------------- #

def test_compatibility_xref_append_writes_nothing():
    base = _base_pdf()
    full, start, end = _append_compatibility_xref(base)
    written = objects_written_in_increment(full, start, end)
    assert written == set()


def test_microsoft_clear_final_increment_is_zero_writes():
    """The real Microsoft hybrid file's 184-byte final increment writes nothing."""
    from pdf_forgery.revision_recovery.reconstruct import reconstruct_from_path

    import os

    sample = os.path.join("test_pdf's", "Microsoft-Sample-Invoice_clear.pdf")
    if not os.path.exists(sample):
        pytest.skip("Microsoft-Sample-Invoice_clear.pdf sample not present")

    rec = reconstruct_from_path(sample)
    assert rec.revision_count == 2
    rev_a, rev_b = rec.revisions
    written = objects_written_in_increment(
        rev_b.data, len(rev_a.data), len(rev_b.data)
    )
    assert written == set()


# --------------------------------------------------------------------------- #
# Defensive contract
# --------------------------------------------------------------------------- #

def test_invalid_ranges_return_empty():
    raw = _base_pdf()
    assert objects_written_in_increment(raw, -1, 10) == set()
    assert objects_written_in_increment(raw, 10, 10) == set()
    assert objects_written_in_increment(raw, 10, 5) == set()
    assert objects_written_in_increment(raw, 0, len(raw) + 100) == set()


def test_garbage_increment_never_raises():
    raw = b"%PDF-1.7\n" + b"garbage \x00\x01\x02 startxref 999999 %%EOF"
    # Should not raise and should not invent objects.
    assert objects_written_in_increment(raw, 9, len(raw)) == set()
