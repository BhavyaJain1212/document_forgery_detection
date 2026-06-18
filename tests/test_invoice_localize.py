"""Tests for invoice_arithmetic bounding-box localization.

These tests verify that:
  - InvoiceReport carries page_dims / page_rotations
  - aggregate()._finding_bbox produces the expected canonical BBox for an
    invoice finding (output cell bbox normalized from PDF user space)
  - Degradation paths (sentinel bbox, missing page dims, guard mismatch) all
    return bbox=None without raising
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pdf_forgery.aggregate import aggregate
from pdf_forgery.aggregate.aggregate import _finding_bbox
from pdf_forgery.aggregate.models import BBox
from pdf_forgery.core.geometry import pdf_bbox_to_canonical
from pdf_forgery.core.types import ConfidenceTier, Finding, StageResult
from pdf_forgery.invoice_arithmetic.models import (
    ArithmeticFinding,
    ArithmeticFindingKind,
    ColumnRole,
    InvoiceReport,
    RelationshipKind,
    SegmentationConfidence,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _arith_finding(
    page_index: int = 0,
    bbox: tuple[float, float, float, float] = (100.0, 200.0, 250.0, 220.0),
    high_value=None,
) -> ArithmeticFinding:
    return ArithmeticFinding(
        page_index=page_index,
        kind=ArithmeticFindingKind.BROKEN_RELATIONSHIP,
        tier=ConfidenceTier.MEDIUM,
        relationship_kind=RelationshipKind.LINE_ITEM,
        equation_text="1*100 != 200",
        expected=100.0,
        stated=200.0,
        delta=100.0,
        is_gross=True,
        reason="test",
        bbox=bbox,
        high_value=high_value,
    )


def _core_finding(page: int = 0, high_value: str | None = None) -> Finding:
    return Finding(
        stage="invoice_arithmetic",
        tier=ConfidenceTier.MEDIUM,
        reason="test",
        page=page,
        high_value=high_value,
    )


def _report(findings, page_dims=(), page_rotations=()) -> InvoiceReport:
    return InvoiceReport(
        path="<test>",
        ok=True,
        tier=ConfidenceTier.MEDIUM,
        score=65,
        findings=tuple(findings),
        page_dims=page_dims,
        page_rotations=page_rotations,
    )


def _stage_result(report: InvoiceReport, findings=()) -> StageResult:
    return StageResult(
        stage="invoice_arithmetic",
        tier=report.tier,
        score=report.score,
        findings=tuple(findings),
        summary="",
        reasons=(),
        notes=(),
        ok=True,
        payload=report,
    )


# ---------------------------------------------------------------------------
# core/geometry.py unit tests
# ---------------------------------------------------------------------------

class TestPdfBboxToCanonical:
    def test_r0_known_position(self):
        # Page 612×792; bbox at bottom-left origin (100, 200, 250, 220) in PDF space
        # R=0: px=(100, 792-220, 250, 792-200) = (100, 572, 250, 592); rw=612, rh=792
        result = pdf_bbox_to_canonical(
            (100.0, 200.0, 250.0, 220.0),
            page_width_pt=612.0, page_height_pt=792.0, rotate=0,
        )
        assert result is not None
        x0, y0, x1, y1 = result
        assert x0 == pytest.approx(100 / 612)
        assert y0 == pytest.approx((792 - 220) / 792)
        assert x1 == pytest.approx(250 / 612)
        assert y1 == pytest.approx((792 - 200) / 792)
        assert y0 < y1  # top-left origin: y0 is the upper edge

    def test_r0_result_in_unit_range(self):
        result = pdf_bbox_to_canonical(
            (0.0, 0.0, 612.0, 792.0),
            page_width_pt=612.0, page_height_pt=792.0, rotate=0,
        )
        assert result == pytest.approx((0.0, 0.0, 1.0, 1.0))

    def test_r90_result_in_unit_range(self):
        result = pdf_bbox_to_canonical(
            (100.0, 200.0, 250.0, 300.0),
            page_width_pt=612.0, page_height_pt=792.0, rotate=90,
        )
        assert result is not None
        for v in result:
            assert 0.0 <= v <= 1.0

    def test_r180_result_in_unit_range(self):
        result = pdf_bbox_to_canonical(
            (100.0, 200.0, 250.0, 300.0),
            page_width_pt=612.0, page_height_pt=792.0, rotate=180,
        )
        assert result is not None
        for v in result:
            assert 0.0 <= v <= 1.0

    def test_r270_result_in_unit_range(self):
        result = pdf_bbox_to_canonical(
            (100.0, 200.0, 250.0, 300.0),
            page_width_pt=612.0, page_height_pt=792.0, rotate=270,
        )
        assert result is not None
        for v in result:
            assert 0.0 <= v <= 1.0

    def test_zero_page_dims_returns_none(self):
        assert pdf_bbox_to_canonical((0, 0, 10, 10), page_width_pt=0, page_height_pt=792) is None
        assert pdf_bbox_to_canonical((0, 0, 10, 10), page_width_pt=612, page_height_pt=0) is None

    def test_invalid_rotation_returns_none(self):
        assert pdf_bbox_to_canonical((0, 0, 10, 10), page_width_pt=612, page_height_pt=792, rotate=45) is None

    def test_clamping_keeps_offpage_in_range(self):
        result = pdf_bbox_to_canonical(
            (-10.0, -10.0, 700.0, 900.0),
            page_width_pt=612.0, page_height_pt=792.0, rotate=0,
        )
        assert result is not None
        for v in result:
            assert 0.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# invoice_arithmetic _finding_bbox
# ---------------------------------------------------------------------------

class TestInvoiceFindingBbox:
    def test_known_position_bbox_normalized_correctly(self):
        # PDF page 612×792; cell at (100, 200, 250, 220) in PDF bottom-left space
        rf = _arith_finding(page_index=0, bbox=(100.0, 200.0, 250.0, 220.0))
        report = _report([rf], page_dims=((612.0, 792.0),), page_rotations=(0,))
        core = _core_finding(page=0)
        sr = _stage_result(report, findings=[core])

        result = _finding_bbox(sr, core, 0)
        assert result is not None
        assert isinstance(result, BBox)
        # expected: x0=100/612, y0=(792-220)/792, x1=250/612, y1=(792-200)/792
        assert result.x0 == pytest.approx(100 / 612, abs=0.02)
        assert result.y0 == pytest.approx((792 - 220) / 792, abs=0.02)
        assert result.x1 == pytest.approx(250 / 612, abs=0.02)
        assert result.y1 == pytest.approx((792 - 200) / 792, abs=0.02)
        assert result.y0 < result.y1  # top-left orientation

    def test_sentinel_bbox_returns_none(self):
        rf = _arith_finding(bbox=(0.0, 0.0, 0.0, 0.0))
        report = _report([rf], page_dims=((612.0, 792.0),))
        core = _core_finding(page=0)
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_missing_page_dims_returns_none(self):
        rf = _arith_finding(bbox=(100.0, 200.0, 250.0, 220.0))
        report = _report([rf], page_dims=())  # no dims
        core = _core_finding(page=0)
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_page_out_of_dims_range_returns_none(self):
        rf = _arith_finding(page_index=2, bbox=(100.0, 200.0, 250.0, 220.0))
        report = _report([rf], page_dims=((612.0, 792.0),))  # only page 0
        core = _core_finding(page=2)
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 2) is None

    def test_page_guard_mismatch_returns_none(self):
        rf = _arith_finding(page_index=0, bbox=(100.0, 200.0, 250.0, 220.0))
        report = _report([rf], page_dims=((612.0, 792.0),))
        core = _core_finding(page=5)  # page disagrees with rich finding
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_high_value_guard_mismatch_returns_none(self):
        from pdf_forgery.revision_recovery.models import HighValueKind
        rf = _arith_finding(bbox=(100.0, 200.0, 250.0, 220.0), high_value=HighValueKind.AMOUNT)
        report = _report([rf], page_dims=((612.0, 792.0),))
        core = _core_finding(page=0, high_value="date")  # wrong high_value
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_rotation_90_stays_in_unit_range(self):
        rf = _arith_finding(bbox=(100.0, 200.0, 250.0, 220.0))
        report = _report([rf], page_dims=((612.0, 792.0),), page_rotations=(90,))
        core = _core_finding(page=0)
        sr = _stage_result(report, findings=[core])
        result = _finding_bbox(sr, core, 0)
        assert result is not None
        for v in (result.x0, result.y0, result.x1, result.y1):
            assert 0.0 <= v <= 1.0

    def test_no_payload_returns_none(self):
        sr = StageResult(
            stage="invoice_arithmetic", tier=ConfidenceTier.MEDIUM, score=65,
            findings=(), summary="", reasons=(), notes=(), ok=True, payload=None,
        )
        core = _core_finding()
        assert _finding_bbox(sr, core, 0) is None


# ---------------------------------------------------------------------------
# via aggregate() end-to-end
# ---------------------------------------------------------------------------

def test_aggregate_invoice_finding_bbox_populated():
    rf = _arith_finding(page_index=0, bbox=(100.0, 200.0, 250.0, 220.0))
    report = _report([rf], page_dims=((612.0, 792.0),), page_rotations=(0,))
    core = _core_finding(page=0)
    sr = _stage_result(report, findings=[core])

    agg = aggregate([sr])
    inv_findings = [f for f in agg.findings if f.stage == "invoice_arithmetic"]
    assert len(inv_findings) == 1
    bbox = inv_findings[0].bbox
    assert bbox is not None
    # x0 = 100/612 ≈ 0.163
    assert bbox.x0 == pytest.approx(100 / 612, abs=0.02)
    assert bbox.y0 < bbox.y1  # top-left orientation confirmed


def test_aggregate_invoice_finding_bbox_none_without_dims():
    rf = _arith_finding(page_index=0, bbox=(100.0, 200.0, 250.0, 220.0))
    report = _report([rf])  # no page_dims
    core = _core_finding(page=0)
    sr = _stage_result(report, findings=[core])

    agg = aggregate([sr])
    assert agg.findings[0].bbox is None


# ---------------------------------------------------------------------------
# InvoiceReport carries dims after analyze_bytes
# ---------------------------------------------------------------------------

def test_analyze_bytes_populates_page_dims(invoice_clean_pdf):
    from pdf_forgery.invoice_arithmetic import analyze_path
    report = analyze_path(invoice_clean_pdf)
    assert report.ok is True
    assert len(report.page_dims) > 0
    for w, h in report.page_dims:
        assert w > 0
        assert h > 0


def test_analyze_bytes_localization_disabled_returns_empty_dims():
    from pdf_forgery.invoice_arithmetic import analyze_bytes
    from pdf_forgery.invoice_arithmetic.config import InvoiceConfig

    # Minimal valid PDF - just use a blank one page doc via pikepdf
    import io
    import pikepdf
    pdf = pikepdf.Pdf.new()
    page = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=pikepdf.Array([0, 0, 612, 792]),
        Resources=pikepdf.Dictionary(),
    ))
    pdf.Root.Pages.Kids.append(page)
    pdf.Root.Pages.Count = 1
    buf = io.BytesIO()
    pdf.save(buf)
    raw = buf.getvalue()

    cfg = InvoiceConfig(enable_localization=False)
    report = analyze_bytes(raw, config=cfg)
    assert report.page_dims == ()
    assert report.page_rotations == ()
