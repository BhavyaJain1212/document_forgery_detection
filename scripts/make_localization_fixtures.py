#!/usr/bin/env python3
"""Deterministic fixtures for the added-text LOCALIZATION feature.

Each builder returns raw PDF bytes; an incremental update overrides the single
page's content stream (original objects preserved as a byte-prefix — the "Save"
hallmark) so ``revision_recovery`` recovers a prior + current revision and the
localizer can diff their word boxes.

Builders here are imported directly by ``tests/test_locate.py`` (no files are
written by default), mirroring how ``make_fixtures`` is consumed by the suite.

Layout is explicit (known start_y / leading) so a test can reason about which
line a located box must — or must NOT — fall on:

* ``reflow_pair()``    — an inserted distinct line ("Adjusted 777") pushes two
                         identical "... 500" lines down past the position
                         tolerance; only the inserted tokens may be boxed.
* ``rotated_pair()``   — a ``/Rotate 90`` page with an in-place amount edit, to
                         validate rotation-aware coordinates end to end.
* ``amount_pair()``    — a plain in-place amount edit (5,000 -> 50,000).
"""

from __future__ import annotations

import io

import pikepdf
from pikepdf import Array, Dictionary, Name, Pdf

_START_Y = 720
_LEADING = 36
_X = 72
_SIZE = 16


def _content_stream(lines: list[str]) -> bytes:
    """A single text block: ``lines[0]`` at ``_START_Y``, each next ``_LEADING`` lower."""
    ops = ["BT", f"/F1 {_SIZE} Tf", f"{_X} {_START_Y} Td", f"({lines[0]}) Tj"]
    for line in lines[1:]:
        ops.append(f"0 -{_LEADING} Td")
        ops.append(f"({line}) Tj")
    ops.append("ET")
    return ("\n".join(ops)).encode("latin-1")


def build_doc(lines: list[str], *, rotate: int = 0) -> bytes:
    """Single-revision PDF rendering *lines*, optionally with a ``/Rotate`` value."""
    pdf = Pdf.new()
    stream = pdf.make_stream(_content_stream(lines))
    font = pdf.make_indirect(
        Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
    )
    fields = dict(
        Type=Name.Page,
        MediaBox=Array([0, 0, 612, 792]),
        Contents=stream,
        Resources=Dictionary(Font=Dictionary(F1=font)),
    )
    if rotate:
        fields["Rotate"] = rotate
    page = pdf.make_indirect(Dictionary(**fields))
    pdf.Root.Pages.Kids.append(page)
    pdf.Root.Pages.Count = 1

    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    return buf.getvalue()


def append_increment(clean: bytes, new_lines: list[str]) -> bytes:
    """Append a genuine incremental update overriding the page content stream.

    The original ``clean`` bytes are preserved verbatim at the front; only the
    new object + xref + ``/Prev`` trailer + ``startxref`` + ``%%EOF`` are appended.
    """
    from pdf_forgery.revision_recovery.detect import detect

    prev_startxref = detect(clean).valid_boundaries[-1].startxref.pointer

    with pikepdf.open(io.BytesIO(clean)) as p:
        size = int(p.trailer.Size)
        root_num, root_gen = p.Root.objgen
        obj_num, obj_gen = p.pages[0].Contents.objgen

    body = _content_stream(new_lines)
    new_obj = (
        f"{obj_num} {obj_gen} obj\n<< /Length {len(body)} >>\nstream\n".encode("latin-1")
        + body
        + b"\nendstream\nendobj\n"
    )
    obj_offset = len(clean)
    xref_offset = obj_offset + len(new_obj)
    xref = (
        b"xref\n"
        + f"{obj_num} 1\n".encode("latin-1")
        + f"{obj_offset:010d} {obj_gen:05d} n \n".encode("latin-1")
        + b"trailer\n"
        + (
            f"<< /Size {size} /Root {root_num} {root_gen} R /Prev {prev_startxref} >>\n"
        ).encode("latin-1")
        + b"startxref\n"
        + f"{xref_offset}\n".encode("latin-1")
        + b"%%EOF\n"
    )
    return clean + new_obj + xref


# --------------------------------------------------------------------------- #
# Named fixtures (clean, forged) byte pairs                                    #
# --------------------------------------------------------------------------- #

def reflow_pair() -> tuple[bytes, bytes]:
    """Insertion of a distinct line that reflows two identical "500" lines down."""
    clean = build_doc(["Reference 500", "Balance 500"])
    forged = append_increment(clean, ["Adjusted 777", "Reference 500", "Balance 500"])
    return clean, forged


def rotated_pair(rotate: int = 90) -> tuple[bytes, bytes]:
    """A rotated page with an in-place amount edit (Rs 5,000 -> Rs 50,000)."""
    clean = build_doc(["Claim advice", "Approved amount Rs 5,000"], rotate=rotate)
    forged = append_increment(clean, ["Claim advice", "Approved amount Rs 50,000"])
    return clean, forged


def amount_pair() -> tuple[bytes, bytes]:
    """A plain in-place amount edit on an upright page."""
    clean = build_doc(["Claim advice", "Approved amount Rs 5,000"])
    forged = append_increment(clean, ["Claim advice", "Approved amount Rs 50,000"])
    return clean, forged
