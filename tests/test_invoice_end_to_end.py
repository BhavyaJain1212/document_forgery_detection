"""End-to-end invoice-arithmetic tests on real PDFs + generated fixtures.

Calibration note (owner decision, 2026-06-15): the shipped
``Microsoft-Sample-Invoice.pdf`` is NOT a clean arithmetic baseline — it carries
a genuine line-item break (9*41.61 != 37004.49, confirmed contiguous uniform
Calibri glyphs, present in BOTH the clean and tampered copies). So both Microsoft
files are positives here; the convergence -> HIGH path is proven on the
self-contained fixture, and the real precision baseline (LOW) awaits a pristine
invoice at ``test_pdf's/pristine-invoice.pdf``.
"""

from __future__ import annotations

from pdf_forgery.core import ConfidenceTier
from pdf_forgery.invoice_arithmetic import (
    analyze_bytes,
    analyze_path,
    render_json,
    render_summary,
)
from pdf_forgery.invoice_arithmetic.models import RelationshipKind


# --------------------------------------------------------------------------- #
# Generated fixtures: clean -> LOW, convergence tamper -> HIGH
# --------------------------------------------------------------------------- #

def test_clean_fixture_reconciles_low(invoice_clean_pdf):
    report = analyze_path(invoice_clean_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.LOW
    assert report.findings == ()
    # Real taxes + discount + rounding all reconcile.
    assert any(r.kind is RelationshipKind.GRAND_TOTAL for r in report.relationships)
    assert all(r.within_tolerance for r in report.relationships)


def test_convergence_tamper_is_high_localized(invoice_tamper_pdf):
    report = analyze_path(invoice_tamper_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.HIGH
    assert report.score == 92

    high = [f for f in report.findings if f.tier is ConfidenceTier.HIGH]
    assert len(high) == 1
    f = high[0]
    assert f.relationship_kind is RelationshipKind.LINE_ITEM
    assert f.convergence_count >= 2
    assert f.cell_text == "30000.00"           # localized to the edited amount
    assert f.expected == 300.0
    assert f.stated == 30000.0
    assert f.high_value is not None
    # The broken subtotal corroborates (converges on the same cell).
    assert any("subtotal" in c for c in f.corroborating)


# --------------------------------------------------------------------------- #
# Real Sejda-tampered invoice: flags 3.00 * 83.23 != 24019.69, localized.
# --------------------------------------------------------------------------- #

def test_sejda_tamper_flags_broken_line_item(sejda_tampered_pdf):
    report = analyze_path(sejda_tampered_pdf)
    assert report.ok is True
    # No extractable subtotal on the page -> lone breaks -> strong MEDIUM ceiling
    # (the owner's convergence-gated decision; the prompt accepts strong MEDIUM).
    assert report.tier is ConfidenceTier.MEDIUM
    assert report.score >= 60

    tamper = [
        f for f in report.findings
        if abs(f.stated - 24019.69) < 0.01 and abs(f.expected - 249.69) < 0.01
    ]
    assert len(tamper) == 1
    f = tamper[0]
    assert f.relationship_kind is RelationshipKind.LINE_ITEM
    assert f.cell_text == "24019.69"           # localized to the amount cell
    assert f.is_gross is True
    assert "83.23" in f.equation_text and "3" in f.equation_text
    assert f.logical_invoice_id == "invoice-001"
    assert f.page_number == 1
    assert f.bbox != (0.0, 0.0, 0.0, 0.0)
    assert f.role_label
    assert f.equation_kind == RelationshipKind.LINE_ITEM.value
    assert f.segmentation_basis


def test_microsoft_sample_has_genuine_broken_row(microsoft_clean_pdf):
    # The Microsoft sample is itself arithmetically broken (37004.49 row); it is
    # NOT a clean arithmetic baseline. Detector must surface that row truthfully.
    report = analyze_path(microsoft_clean_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.MEDIUM
    broken = [f for f in report.findings if abs(f.stated - 37004.49) < 0.01]
    assert len(broken) == 1
    assert broken[0].expected == 374.49


# --------------------------------------------------------------------------- #
# Robustness: malformed / non-invoice input -> INCONCLUSIVE, not a false HIGH.
# --------------------------------------------------------------------------- #

def test_garbage_bytes_inconclusive():
    report = analyze_bytes(b"%PDF-1.4 not really a pdf", "<garbage>")
    assert report.ok is True
    assert report.tier is ConfidenceTier.INCONCLUSIVE
    assert report.findings == ()


def test_non_invoice_text_inconclusive(clean_pdf):
    # The revision-recovery clean fixture is prose, not a labelled invoice table.
    report = analyze_path(clean_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.INCONCLUSIVE


def test_renderers_do_not_crash(invoice_tamper_pdf):
    report = analyze_path(invoice_tamper_pdf)
    assert "invoice_arithmetic" in render_json(report)
    summary = render_summary(report)
    assert "ADVISORY" in summary
    assert "30000" in summary


def test_pristine_invoice_low_on_arithmetic(pristine_invoice_pdf):
    # Skips until a genuine untouched invoice is provided (see conftest). This is
    # the real precision proof requested by the owner.
    report = analyze_path(pristine_invoice_pdf)
    assert report.ok is True
    assert report.tier in (ConfidenceTier.LOW, ConfidenceTier.INCONCLUSIVE)
    assert not any(f.tier is ConfidenceTier.HIGH for f in report.findings)
