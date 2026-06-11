#!/usr/bin/env python3
"""Generate Stage-1 test fixtures (deterministic).

Produces two PDFs in ``tests/fixtures/`` (override with a positional arg):

  clean.pdf
      A single-revision PDF containing prose plus a currency amount.
      This is the **known-negative**: one revision -> INCONCLUSIVE.

  edited_incremental.pdf
      ``clean.pdf`` with a genuine **incremental update** appended that
      overrides the page content stream to change the amount
      (``Rs 5,000`` -> ``Rs 50,000``) while preserving every original object.
      This is the **known-positive**: substantive text change in a CONTENT
      object -> HIGH (high-value amount altered).

Why a hand-written incremental update instead of ``Pdf.save``: pikepdf/qpdf
rewrite the whole file on save, which collapses the revision history. A forgery
detector that recovers history needs a file where the *original* objects still
sit before the appended revision — exactly what "Save" (not "Save As") produces
in a real editor. We therefore append the new object + xref + ``/Prev`` trailer
+ ``startxref`` + ``%%EOF`` to the original bytes ourselves.

Output is deterministic: ``deterministic_id=True`` derives the ``/ID`` from the
content, and ``Pdf.new()`` writes no creation/modification dates. Re-running the
script byte-for-byte reproduces the fixtures.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, Pdf

# Repo root = parent of this script's directory (scripts/).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DEST = _REPO_ROOT / "tests" / "fixtures"

# The amount edited by the forgery. Kept as named constants so Task-8 tests can
# import and assert on them rather than hard-coding strings.
ORIGINAL_AMOUNT = "Rs 5,000"
FORGED_AMOUNT = "Rs 50,000"


def _content_stream(amount: str) -> bytes:
    """Page content stream: a few prose lines plus the claim amount."""
    lines = [
        "Care Health Insurance - Claim Settlement Advice",
        "Policy No: CHI1234567",
        "Insured: John Doe",
        f"Approved claim amount: {amount}",
    ]
    ops = ["BT", "/F1 16 Tf", "72 720 Td", f"({lines[0]}) Tj"]
    for line in lines[1:]:
        ops.append("0 -36 Td")
        ops.append(f"({line}) Tj")
    ops.append("ET")
    return ("\n".join(ops)).encode("latin-1")


def build_clean(amount: str = ORIGINAL_AMOUNT) -> bytes:
    """Build a single-revision PDF whose content stream contains *amount*."""
    pdf = Pdf.new()
    stream = pdf.make_stream(_content_stream(amount))
    font = pdf.make_indirect(
        Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
    )
    page = pdf.make_indirect(
        Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, 612, 792]),
            Contents=stream,
            Resources=Dictionary(Font=Dictionary(F1=font)),
        )
    )
    pdf.Root.Pages.Kids.append(page)
    pdf.Root.Pages.Count = 1

    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    return buf.getvalue()


def build_forged(clean: bytes, new_amount: str = FORGED_AMOUNT) -> bytes:
    """Append a genuine incremental update overriding the page content stream.

    The original ``clean`` bytes are preserved verbatim at the front of the
    output; only new bytes are appended — the hallmark of an incremental "Save".
    """
    # The startxref of the original revision becomes this revision's /Prev.
    from pdf_forgery.revision_recovery.detect import detect

    prev_startxref = detect(clean).valid_boundaries[-1].startxref.pointer

    with pikepdf.open(io.BytesIO(clean)) as p:
        size = int(p.trailer.Size)
        root_num, root_gen = p.Root.objgen
        obj_num, obj_gen = p.pages[0].Contents.objgen

    body = _content_stream(new_amount)
    new_obj = (
        f"{obj_num} {obj_gen} obj\n<< /Length {len(body)} >>\nstream\n".encode("latin-1")
        + body
        + b"\nendstream\nendobj\n"
    )
    obj_offset = len(clean)
    xref_offset = obj_offset + len(new_obj)

    # A minimal classic xref subsection for just the overridden object, plus a
    # trailer chained to the previous revision via /Prev.
    xref = (
        b"xref\n"
        + f"{obj_num} 1\n".encode("latin-1")
        + f"{obj_offset:010d} {obj_gen:05d} n \n".encode("latin-1")
        + b"trailer\n"
        + (
            f"<< /Size {size} /Root {root_num} {root_gen} R "
            f"/Prev {prev_startxref} >>\n"
        ).encode("latin-1")
        + b"startxref\n"
        + f"{xref_offset}\n".encode("latin-1")
        + b"%%EOF\n"
    )
    return clean + new_obj + xref


def write_fixtures(dest: Path = _DEFAULT_DEST) -> dict[str, Path]:
    """Generate the fixtures into *dest*; return a name -> path map."""
    dest.mkdir(parents=True, exist_ok=True)

    clean = build_clean()
    forged = build_forged(clean)

    clean_path = dest / "clean.pdf"
    forged_path = dest / "edited_incremental.pdf"
    clean_path.write_bytes(clean)
    forged_path.write_bytes(forged)

    return {"clean": clean_path, "edited_incremental": forged_path}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dest = Path(args[0]) if args else _DEFAULT_DEST

    paths = write_fixtures(dest)
    for name, path in paths.items():
        size = path.stat().st_size
        print(f"wrote {name:>18}: {path}  ({size} bytes)")
    print(
        f"\nknown-negative: {paths['clean'].name} (single revision)\n"
        f"known-positive: {paths['edited_incremental'].name} "
        f"({ORIGINAL_AMOUNT!r} -> {FORGED_AMOUNT!r} via incremental update)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
