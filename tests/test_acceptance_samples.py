"""End-to-end acceptance tests on the real sample PDFs for both bug fixes.

Acrobat_Demo_File.pdf is the known-positive: the amount 1871.23 was edited to
18071.23 by inserting a '0' in a different font, and the file carries two
revisions. Microsoft-Sample-Invoice.pdf is the clean known-negative. These
Most samples live (untracked) in ``test_pdf's/``; the W-2 regression sample
lives in ``test_files/``. The tests skip when their sample is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_forgery.core import ConfidenceTier
from pdf_forgery.font_forensics import analyze_path as font_analyze
from pdf_forgery.font_forensics.models import FontFindingKind, HighValueKind
from pdf_forgery.ocr_crosscheck.analyze import analyze_path as ocr_analyze
from pdf_forgery.ocr_crosscheck.models import DivergenceType
from pdf_forgery.ocr_crosscheck.ocr_engine import PaddleOCREngine
from pdf_forgery.revision_recovery import analyze_path as rev_analyze


_TEST_FILES = Path(__file__).resolve().parent.parent / "test_files"


# --------------------------------------------------------------------------- #
# FIX 1 — font_forensics catches the mixed-font amount on the Acrobat file
# --------------------------------------------------------------------------- #

def test_acrobat_font_forensics_high_on_mixed_amount(acrobat_pdf):
    report = font_analyze(acrobat_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.HIGH

    mixes = [f for f in report.findings if f.kind is FontFindingKind.INTRA_TOKEN_FONT_MIX]
    assert len(mixes) == 1
    f = mixes[0]
    assert f.token == "18071.23"
    assert f.suspicious_text == "0"
    assert f.high_value is HighValueKind.AMOUNT
    assert f.token_font == "YWNRZS+Calibri"
    assert f.minority_font == "SUMSRI+SourceSansPro-Regular"
    assert f.page_index == 0


# --------------------------------------------------------------------------- #
# FIX 2 — revision_recovery surfaces 1871.23 -> 18071.23 via glyph fallback
# --------------------------------------------------------------------------- #

def test_acrobat_revision_recovery_high_via_fallback(acrobat_pdf):
    report = rev_analyze(acrobat_pdf)
    assert report.scoring.tier is ConfidenceTier.HIGH
    assert report.scoring.score == 95

    amount_findings = [
        f for f in report.findings
        if f.high_value_kind is HighValueKind.AMOUNT
    ]
    assert amount_findings, "expected a high-value amount finding"
    f = amount_findings[0]
    assert f.before_text == "1871.23"
    assert f.after_text == "18071.23"
    assert f.object_ids, "amount change should map to a changed CONTENT object"
    assert any("fallback" in n for n in report.notes)


# --------------------------------------------------------------------------- #
# Clean invoice stays LOW / no suspicious findings on both stages
# --------------------------------------------------------------------------- #

def test_microsoft_clean_font_forensics_low(microsoft_clean_pdf):
    report = font_analyze(microsoft_clean_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.LOW
    assert not any(
        f.kind is FontFindingKind.INTRA_TOKEN_FONT_MIX for f in report.findings
    )


def test_microsoft_clean_revision_recovery_inconclusive(microsoft_clean_pdf):
    report = rev_analyze(microsoft_clean_pdf)
    # Single-revision clean file -> revision recovery has nothing to compare.
    assert report.scoring.tier is ConfidenceTier.INCONCLUSIVE
    assert not any("fallback" in n for n in report.notes)


def test_microsoft_hybrid_revision_recovery_not_medium(microsoft_hybrid_pdf):
    """The hybrid-reference file's 184-byte compatibility xref append is a second
    %%EOF that authors ZERO objects. Revision recovery must NOT raise a phantom
    "content changed" MEDIUM off a truncated-revision enumeration diff; the
    increment is recognized as a benign cross-reference rebuild (LOW), or
    INCONCLUSIVE if a boundary cannot be reconstructed."""
    report = rev_analyze(microsoft_hybrid_pdf)
    assert report.ok is True
    assert report.scoring.tier in (ConfidenceTier.LOW, ConfidenceTier.INCONCLUSIVE)
    # No phantom CONTENT-change findings.
    assert not any(f.object_classes for f in report.findings) or all(
        "content stream changed but text layer is unchanged" not in f.summary
        for f in report.findings
    )


def test_page4_microsoft_abn_is_not_a_font_high(page4_microsoft_pdf):
    report = font_analyze(page4_microsoft_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.LOW
    assert report.score == 15
    assert report.findings == ()


def test_sejda_tampered_font_behavior_unchanged(sejda_tampered_pdf):
    report = font_analyze(sejda_tampered_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.LOW
    assert report.score == 15
    assert report.findings == ()


def test_clean_w2_not_high_on_fonts():
    """A homogeneous data-entry font on a clean W-2 is a form convention."""
    path = _TEST_FILES / "W2_XL_input_clean_1000.pdf"
    if not path.exists():
        pytest.skip(f"sample not available: {path}")

    report = font_analyze(path)

    assert report.ok is True
    assert report.tier in (ConfidenceTier.LOW, ConfidenceTier.INCONCLUSIVE)
    assert not any(
        f.kind is FontFindingKind.PAGE_BASELINE_DEVIATION
        for f in report.findings
    )


def test_clean_w2_ocr_crosscheck_not_high():
    """Form reading-order and logo OCR artifacts are not content divergence."""
    path = _TEST_FILES / "W2_XL_input_clean_1000.pdf"
    if not path.exists():
        pytest.skip(f"sample not available: {path}")

    engine = PaddleOCREngine()
    if not engine.is_available():
        pytest.skip("PaddleOCR not installed/available in this environment")

    report = ocr_analyze(str(path), engine=engine)

    assert report.ok is True
    assert report.result is not None
    assert report.result.tier in (ConfidenceTier.LOW, ConfidenceTier.INCONCLUSIVE)

    non_agree = [
        d for d in report.result.divergences
        if d.type is not DivergenceType.AGREE
    ]
    texts = [
        " ".join(w.text for w in d.embedded)
        or (d.ocr.text if d.ocr is not None else "")
        for d in non_agree
    ]
    assert not any("Statutory" in text for text in texts)
    assert "IRSefile" not in texts


# --------------------------------------------------------------------------- #
# OCR cross-check precision baseline (see docs/STAGE3_OCR_FALSE_POSITIVE_FIX.md)
# --------------------------------------------------------------------------- #

def test_page4_microsoft_ocr_crosscheck_not_high(page4_microsoft_pdf):
    """Real-engine regression for the OCR false-positive bug.

    Before the fix, PaddleOCR's default document-unwarping warped every OCR box
    out of the rendered raster's coordinate space, and near-universal "ID-like"
    classification turned routine OCR noise into zero-tolerance high-value
    divergence — this clean invoice scored HIGH 95. With doc-unwarping/
    orientation disabled and orphan/ID scoring corroboration-gated, it must not
    score HIGH."""
    engine = PaddleOCREngine()
    if not engine.is_available():
        pytest.skip("PaddleOCR not installed/available in this environment")
    report = ocr_analyze(str(page4_microsoft_pdf), engine=engine)
    assert report.ok is True
    assert report.result is not None
    assert report.result.tier in (ConfidenceTier.LOW, ConfidenceTier.INCONCLUSIVE)


def test_microsoft_long_invoice_ocr_crosscheck_not_high(microsoft_hybrid_pdf):
    """Real-engine regression for the long-PDF OCR false positive
    (docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md).

    Before the fix, this clean 13-page invoice scored HIGH 95: a bare digit
    (a reservation term, e.g. "3") inside Azure SKU lines elevated the whole
    line to AMOUNT's zero tolerance, so an unrelated underscore-vs-space OCR
    glyph drop tripped 3 false AMOUNT MISMATCHes; and a "Microsoft Azure"
    header recognized by OCR but not the embedded layer on every page
    accumulated an absolute mass that doesn't scale with document length.
    Acceptance criteria (per the design doc): not HIGH and not MEDIUM — LOW
    or INCONCLUSIVE."""
    engine = PaddleOCREngine()
    if not engine.is_available():
        pytest.skip("PaddleOCR not installed/available in this environment")
    report = ocr_analyze(str(microsoft_hybrid_pdf), engine=engine)
    assert report.ok is True
    assert report.result is not None
    assert report.result.tier in (ConfidenceTier.LOW, ConfidenceTier.INCONCLUSIVE)
