#!/usr/bin/env python3
"""Generate deterministic invoice-arithmetic and segmentation fixtures.

All fixtures are ordinary single-revision PDFs (``Pdf.save``) with a UNIFORM font
and no incremental update — exactly the clean-re-render shape that revision
recovery and font forensics cannot flag. The arithmetic relationships are the
only signal.

  invoice_clean.pdf  (KNOWN-NEGATIVE -> LOW)
      A genuine small invoice whose line items, subtotal, discount, CGST/SGST,
      and grand total all reconcile within tolerance. The amount ``249.685`` is
      printed rounded to ``249.69`` to prove legitimate rounding does NOT flag.

  invoice_convergence_tamper.pdf  (KNOWN-POSITIVE -> HIGH)
      The same invoice with ONE line amount inflated (300.00 -> 30000.00) while
      its qty/rate and the printed subtotal/grand-total were left untouched —
      so qty*rate=amount breaks AND sum(amounts)=subtotal breaks AND
      subtotal+tax=grand-total breaks, three equations all reconciled by
      correcting the single edited cell. Convergence -> HIGH, localized to the
      amount cell.

This models the Sejda attack's structural give-away (an edited cell that several
totals still disagree with) in a self-contained, regenerable fixture, since the
real Sejda sample lacks an extractable subtotal to converge on.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from pikepdf import Array, Dictionary, Name, Pdf

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DEST = _REPO_ROOT / "tests" / "fixtures"

# Column x-anchors (PDF points). Left-aligned text; the table reconstructor maps
# each number to a column by the midpoint between header centres.
_COL_X = {"desc": 60, "qty": 300, "rate": 360, "amount": 460}
_HEADER_Y = 700
_ROW_DY = 24

# Clean line items: (description, qty, rate, amount-as-printed).
_CLEAN_ROWS = [
    ("Consultation", "2", "100.00", "200.00"),
    ("Medicine", "3", "50.00", "150.00"),
    ("Procedure", "1", "300.00", "300.00"),
    # 249.685 legitimately rounded to 249.69 — must stay within tolerance.
    ("Dressing", "1", "249.685", "249.69"),
]
# Summary block (label, value), printed below the table.
_CLEAN_SUMMARY = [
    ("Subtotal", "899.69"),     # 200 + 150 + 300 + 249.69
    ("Discount", "49.69"),      # -> taxable 850.00
    ("CGST", "76.50"),          # 9% of 850
    ("SGST", "76.50"),
    ("Grand Total", "1003.00"),  # 899.69 - 49.69 + 76.50 + 76.50
]

# The forged variant inflates the "Procedure" amount but leaves qty/rate and all
# totals untouched (the classic edit-one-cell, forget-the-totals mistake).
FORGED_ROW_INDEX = 2
FORGED_ORIGINAL_AMOUNT = "300.00"
FORGED_AMOUNT = "30000.00"


def _font(pdf: Pdf, basefont: str = "Helvetica") -> object:
    return pdf.make_indirect(
        Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/" + basefont))
    )


def _text_cmd(x: int, y: int, text: str) -> bytes:
    safe = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return f"BT /F1 11 Tf {x} {y} Td ({safe}) Tj ET\n".encode("latin-1")


def _content(
    rows: list[tuple[str, str, str, str]],
    *,
    invoice_id: str | None = "INV-001",
    include_title: bool = True,
    include_header: bool = True,
    summary: list[tuple[str, str]] | None = None,
    continuation: bool = False,
) -> bytes:
    out = bytearray()
    if include_title:
        out += _text_cmd(_COL_X["desc"], 760, "Sunrise Hospital - Tax Invoice")
    if invoice_id:
        out += _text_cmd(_COL_X["desc"], 736, f"Invoice No: {invoice_id}")
    if continuation:
        out += _text_cmd(_COL_X["desc"], 718, "Continued")
    if include_header:
        out += _text_cmd(_COL_X["desc"], _HEADER_Y, "Description")
        out += _text_cmd(_COL_X["qty"], _HEADER_Y, "Qty")
        out += _text_cmd(_COL_X["rate"], _HEADER_Y, "Rate")
        out += _text_cmd(_COL_X["amount"], _HEADER_Y, "Amount")
    # Rows
    y = _HEADER_Y - _ROW_DY
    for desc, qty, rate, amount in rows:
        out += _text_cmd(_COL_X["desc"], y, desc)
        out += _text_cmd(_COL_X["qty"], y, qty)
        out += _text_cmd(_COL_X["rate"], y, rate)
        out += _text_cmd(_COL_X["amount"], y, amount)
        y -= _ROW_DY
    if summary:
        y -= _ROW_DY
        for label, value in summary:
            out += _text_cmd(_COL_X["desc"], y, label)
            out += _text_cmd(_COL_X["amount"], y, value)
            y -= _ROW_DY
    return bytes(out)


def _build_pages(contents: list[bytes]) -> bytes:
    pdf = Pdf.new()
    font = _font(pdf)
    for content in contents:
        stream = pdf.make_stream(content)
        page = pdf.make_indirect(
            Dictionary(
                Type=Name.Page,
                MediaBox=Array([0, 0, 612, 792]),
                Contents=stream,
                Resources=Dictionary(Font=Dictionary(F1=font)),
            )
        )
        pdf.Root.Pages.Kids.append(page)
    pdf.Root.Pages.Count = len(contents)
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    return buf.getvalue()


def _build(rows: list[tuple[str, str, str, str]]) -> bytes:
    return _build_pages([_content(rows, summary=_CLEAN_SUMMARY)])


def build_clean() -> bytes:
    """Known-negative: every relationship reconciles within tolerance."""
    return _build(_CLEAN_ROWS)


def build_forged() -> bytes:
    """Known-positive: one line amount inflated; totals left untouched."""
    rows = [list(r) for r in _CLEAN_ROWS]
    rows[FORGED_ROW_INDEX][3] = FORGED_AMOUNT
    return _build([tuple(r) for r in rows])


def build_two_clean_invoices() -> bytes:
    """Two complete invoices in one PDF; their rows/totals must never mix."""
    first_rows = _CLEAN_ROWS[:2]
    second_rows = _CLEAN_ROWS[2:]
    return _build_pages([
        _content(
            first_rows,
            invoice_id="INV-A",
            summary=[("Subtotal", "350.00"), ("Grand Total", "350.00")],
        ),
        _content(
            second_rows,
            invoice_id="INV-B",
            summary=[("Subtotal", "549.69"), ("Grand Total", "549.69")],
        ),
    ])


def build_repeated_header_continuation() -> bytes:
    """One invoice over two pages with its table header repeated."""
    return _build_pages([
        _content(_CLEAN_ROWS[:2], invoice_id="INV-R", summary=None),
        _content(_CLEAN_ROWS[2:], invoice_id="INV-R", summary=_CLEAN_SUMMARY),
    ])


def build_headerless_continuation() -> bytes:
    """One invoice whose second page has aligned rows but no table header."""
    return _build_pages([
        _content(_CLEAN_ROWS[:2], invoice_id="INV-H", summary=None),
        _content(
            _CLEAN_ROWS[2:],
            invoice_id="INV-H",
            include_title=False,
            include_header=False,
            continuation=True,
            summary=_CLEAN_SUMMARY,
        ),
    ])


def build_ambiguous_multipage() -> bytes:
    """Aligned rows exist on page two, but a fresh title makes ownership unclear."""
    return _build_pages([
        _content(
            _CLEAN_ROWS[:2], invoice_id=None, include_title=True, summary=None
        ),
        _content(
            _CLEAN_ROWS[2:],
            invoice_id=None,
            include_title=True,
            include_header=False,
            summary=[("Subtotal", "549.69")],
        ),
    ])


def write_fixtures(dest: Path = _DEFAULT_DEST) -> dict[str, Path]:
    """Generate the fixtures into *dest*; return a name -> path map."""
    dest.mkdir(parents=True, exist_ok=True)
    clean_path = dest / "invoice_clean.pdf"
    forged_path = dest / "invoice_convergence_tamper.pdf"
    bundle_path = dest / "invoice_two_clean_bundle.pdf"
    repeated_path = dest / "invoice_repeated_header.pdf"
    headerless_path = dest / "invoice_headerless_continuation.pdf"
    ambiguous_path = dest / "invoice_ambiguous_multipage.pdf"
    clean_path.write_bytes(build_clean())
    forged_path.write_bytes(build_forged())
    bundle_path.write_bytes(build_two_clean_invoices())
    repeated_path.write_bytes(build_repeated_header_continuation())
    headerless_path.write_bytes(build_headerless_continuation())
    ambiguous_path.write_bytes(build_ambiguous_multipage())
    return {
        "invoice_clean": clean_path,
        "invoice_convergence_tamper": forged_path,
        "invoice_two_clean_bundle": bundle_path,
        "invoice_repeated_header": repeated_path,
        "invoice_headerless_continuation": headerless_path,
        "invoice_ambiguous_multipage": ambiguous_path,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dest = Path(args[0]) if args else _DEFAULT_DEST
    paths = write_fixtures(dest)
    for name, path in paths.items():
        print(f"wrote {name:>30}: {path}  ({path.stat().st_size} bytes)")
    print(
        f"\nknown-negative: {paths['invoice_clean'].name} (all relationships reconcile)\n"
        f"known-positive: {paths['invoice_convergence_tamper'].name} "
        f"(amount {FORGED_ORIGINAL_AMOUNT} -> {FORGED_AMOUNT}; convergence -> HIGH)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
