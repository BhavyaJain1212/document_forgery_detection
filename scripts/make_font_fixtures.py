#!/usr/bin/env python3
"""Generate font-forensics test fixtures (deterministic, single-revision).

Both fixtures are ordinary single-revision PDFs (``Pdf.save``), so revision
recovery returns INCONCLUSIVE on them — font forensics is the stage that must
tell them apart:

  font_edited_subset.pdf  (KNOWN-POSITIVE -> HIGH)
      One line reads ``Approved claim amount: 50,000`` where a single ``0``
      inside the amount uses ``GHIJKL+Helvetica`` while the other amount glyphs
      use ``ABCDEF+Helvetica`` — the intra-token re-embedding fingerprint.

  font_multifont_invoice.pdf  (KNOWN-NEGATIVE -> LOW)
      A genuine multi-font invoice: bold headers (``Helvetica-Bold``) over body
      text (``Helvetica``). Multiple fonts, but each line is internally uniform
      and the amount is in the body font — ordinary styling, no inconsistency.

To make pdfminer report per-character subset font names with real glyph widths
(needed for line/token geometry) the subset fonts carry a FontDescriptor and a
constant Widths array; no actual font program is embedded, which is enough for
attribution and layout.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from pikepdf import Array, Dictionary, Name, Pdf

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DEST = _REPO_ROOT / "tests" / "fixtures"

# The high-value amount that the positive fixture re-embeds in a foreign subset.
AMOUNT = "50,000"
LABEL_SUBSET = "ABCDEF+Helvetica"
AMOUNT_SUBSET = "GHIJKL+Helvetica"


def _subset_font(pdf: Pdf, name: str) -> object:
    """A Type1 font named *name* with a descriptor + constant widths.

    Enough for pdfminer to attribute the subset name and lay out glyphs; no
    embedded font program is needed for the detector's purposes.
    """
    desc = pdf.make_indirect(
        Dictionary(
            Type=Name.FontDescriptor,
            FontName=Name("/" + name),
            Flags=32,
            FontBBox=Array([0, -200, 1000, 800]),
            ItalicAngle=0,
            Ascent=800,
            Descent=-200,
            CapHeight=700,
            StemV=80,
        )
    )
    return pdf.make_indirect(
        Dictionary(
            Type=Name.Font,
            Subtype=Name.Type1,
            BaseFont=Name("/" + name),
            FirstChar=32,
            LastChar=126,
            Widths=Array([500] * 95),
            FontDescriptor=desc,
        )
    )


def _std_font(pdf: Pdf, basefont: str) -> object:
    """A standard-14 Type1 font (pdfminer has builtin metrics)."""
    return pdf.make_indirect(
        Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/" + basefont))
    )


def _page(pdf: Pdf, content: bytes, fonts: dict[str, object]) -> object:
    stream = pdf.make_stream(content)
    font_dict = Dictionary(**fonts)
    return pdf.make_indirect(
        Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, 612, 792]),
            Contents=stream,
            Resources=Dictionary(Font=font_dict),
        )
    )


def _finish(pdf: Pdf, page: object) -> bytes:
    pdf.Root.Pages.Kids.append(page)
    pdf.Root.Pages.Count = 1
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    return buf.getvalue()


def build_forged() -> bytes:
    """Known-positive: one amount glyph uses a foreign Helvetica subset."""
    pdf = Pdf.new()
    # F1 = label subset, F2 = amount subset (same base, different tag).
    content = (
        b"BT /F1 14 Tf 72 720 Td (Care Health Insurance - Claim Settlement) Tj\n"
        b"0 -28 Td (Policy No: CHI1234567) Tj\n"
        b"0 -28 Td (Approved claim amount: 50,) Tj "
        b"/F2 14 Tf (0) Tj /F1 14 Tf (00) Tj ET"
    )
    fonts = {"F1": _subset_font(pdf, LABEL_SUBSET), "F2": _subset_font(pdf, AMOUNT_SUBSET)}
    return _finish(pdf, _page(pdf, content, fonts))


def build_multifont() -> bytes:
    """Known-negative: bold headers over body text; amount in the body font."""
    pdf = Pdf.new()
    # F1 = bold header face, F2 = regular body face (different family roots share
    # 'Helvetica' so this is ordinary emphasis, not a substitution).
    content = (
        b"BT /F1 18 Tf 72 730 Td (INVOICE) Tj ET\n"
        b"BT /F2 12 Tf 72 700 Td (Care Health Insurance) Tj\n"
        b"0 -24 Td (Policy No: CHI1234567) Tj\n"
        b"0 -24 Td (Approved claim amount: " + AMOUNT.encode("latin-1") + b") Tj ET\n"
        b"BT /F1 14 Tf 72 620 Td (SUMMARY) Tj ET"
    )
    fonts = {"F1": _std_font(pdf, "Helvetica-Bold"), "F2": _std_font(pdf, "Helvetica")}
    return _finish(pdf, _page(pdf, content, fonts))


def write_fixtures(dest: Path = _DEFAULT_DEST) -> dict[str, Path]:
    """Generate the fixtures into *dest*; return a name -> path map."""
    dest.mkdir(parents=True, exist_ok=True)
    forged_path = dest / "font_edited_subset.pdf"
    clean_path = dest / "font_multifont_invoice.pdf"
    forged_path.write_bytes(build_forged())
    clean_path.write_bytes(build_multifont())
    return {"font_edited_subset": forged_path, "font_multifont_invoice": clean_path}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dest = Path(args[0]) if args else _DEFAULT_DEST
    paths = write_fixtures(dest)
    for name, path in paths.items():
        print(f"wrote {name:>24}: {path}  ({path.stat().st_size} bytes)")
    print(
        f"\nknown-positive: {paths['font_edited_subset'].name} "
        f"(one glyph inside amount {AMOUNT!r} uses {AMOUNT_SUBSET} vs majority {LABEL_SUBSET})\n"
        f"known-negative: {paths['font_multifont_invoice'].name} "
        f"(genuine bold-header multi-font invoice)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
