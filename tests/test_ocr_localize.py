"""Tests for ocr_crosscheck bounding-box localization.

Verifies that:
  - OCRCrossCheckReport carries page_dims_px
  - aggregate()._finding_bbox correctly normalizes pixel-space bboxes
  - AGREE-filter alignment: finding index skips AGREE divergences correctly
  - Divergence types MISMATCH/EMBEDDED_ONLY/OCR_ONLY all produce a bbox
  - Degradation and positional guard work correctly
"""

from __future__ import annotations

import pytest

from pdf_forgery.aggregate import aggregate
from pdf_forgery.aggregate.aggregate import _finding_bbox
from pdf_forgery.aggregate.models import BBox
from pdf_forgery.core.geometry import pixel_bbox_to_canonical
from pdf_forgery.core.types import ConfidenceTier, Finding, StageResult
from pdf_forgery.ocr_crosscheck.models import (
    Divergence,
    DivergenceType,
    OCRCrossCheckReport,
    Stage3Result,
    TokenClass,
    WordBox,
    WordSource,
)


# ---------------------------------------------------------------------------
# pixel_bbox_to_canonical unit tests
# ---------------------------------------------------------------------------

class TestPixelBboxToCanonical:
    def test_known_position(self):
        # 1200×1600 page; bbox at (120, 160, 360, 200)
        result = pixel_bbox_to_canonical(
            (120.0, 160.0, 360.0, 200.0),
            page_width_px=1200.0, page_height_px=1600.0,
        )
        assert result is not None
        assert result[0] == pytest.approx(120 / 1200)
        assert result[1] == pytest.approx(160 / 1600)
        assert result[2] == pytest.approx(360 / 1200)
        assert result[3] == pytest.approx(200 / 1600)

    def test_full_page_is_unit_square(self):
        result = pixel_bbox_to_canonical(
            (0.0, 0.0, 1200.0, 1600.0),
            page_width_px=1200.0, page_height_px=1600.0,
        )
        assert result == pytest.approx((0.0, 0.0, 1.0, 1.0))

    def test_zero_dims_returns_none(self):
        assert pixel_bbox_to_canonical(
            (0, 0, 100, 100), page_width_px=0, page_height_px=1600
        ) is None
        assert pixel_bbox_to_canonical(
            (0, 0, 100, 100), page_width_px=1200, page_height_px=0
        ) is None

    def test_clamping_keeps_off_page_in_range(self):
        result = pixel_bbox_to_canonical(
            (-10.0, -20.0, 1500.0, 2000.0),
            page_width_px=1200.0, page_height_px=1600.0,
        )
        assert result is not None
        for v in result:
            assert 0.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wordbox(bbox, page_index=0) -> WordBox:
    return WordBox(text="x", bbox=bbox, source=WordSource.EMBEDDED, conf=None, page_index=page_index)


def _divergence(
    div_type: DivergenceType,
    embedded_bboxes=(),
    ocr_bbox=None,
    page_index: int = 0,
    token_class: TokenClass = TokenClass.PROSE,
) -> Divergence:
    embedded = tuple(_wordbox(b, page_index) for b in embedded_bboxes)
    ocr = _wordbox(ocr_bbox, page_index) if ocr_bbox is not None else None
    return Divergence(
        type=div_type,
        embedded=embedded,
        ocr=ocr,
        token_class=token_class,
        weight=1.0,
        page_index=page_index,
    )


def _core_finding(page: int = 0, high_value: str | None = None) -> Finding:
    return Finding(
        stage="ocr_crosscheck",
        tier=ConfidenceTier.MEDIUM,
        reason="test",
        page=page,
        high_value=high_value,
    )


def _report_with_divergences(
    divergences: list[Divergence],
    page_dims_px=((1200.0, 1600.0),),
) -> OCRCrossCheckReport:
    result = Stage3Result(
        tier=ConfidenceTier.MEDIUM,
        score=50,
        divergences=tuple(divergences),
    )
    return OCRCrossCheckReport(
        path="<test>",
        ok=True,
        result=result,
        page_dims_px=page_dims_px,
    )


def _stage_result(report: OCRCrossCheckReport, findings=()) -> StageResult:
    return StageResult(
        stage="ocr_crosscheck",
        tier=ConfidenceTier.MEDIUM,
        score=50,
        findings=tuple(findings),
        summary="",
        reasons=(),
        notes=(),
        ok=True,
        payload=report,
    )


# ---------------------------------------------------------------------------
# Basic MISMATCH / EMBEDDED_ONLY / OCR_ONLY localization
# ---------------------------------------------------------------------------

class TestOCRFindingBboxBasic:
    def test_mismatch_unions_embedded_and_ocr(self):
        # embedded at (120, 160, 250, 200), ocr at (110, 158, 260, 202)
        # union: (110, 158, 260, 202)
        emb = (120.0, 160.0, 250.0, 200.0)
        ocr = (110.0, 158.0, 260.0, 202.0)
        d = _divergence(DivergenceType.MISMATCH, embedded_bboxes=[emb], ocr_bbox=ocr)
        report = _report_with_divergences([d])
        core = _core_finding(page=0)
        sr = _stage_result(report, findings=[core])

        result = _finding_bbox(sr, core, 0)
        assert result is not None
        assert isinstance(result, BBox)
        assert result.x0 == pytest.approx(110 / 1200, abs=0.01)
        assert result.y0 == pytest.approx(158 / 1600, abs=0.01)
        assert result.x1 == pytest.approx(260 / 1200, abs=0.01)
        assert result.y1 == pytest.approx(202 / 1600, abs=0.01)

    def test_embedded_only_uses_embedded_box(self):
        emb = (120.0, 160.0, 250.0, 200.0)
        d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[emb])
        report = _report_with_divergences([d])
        core = _core_finding()
        sr = _stage_result(report, findings=[core])

        result = _finding_bbox(sr, core, 0)
        assert result is not None
        assert result.x0 == pytest.approx(120 / 1200, abs=0.01)
        assert result.x1 == pytest.approx(250 / 1200, abs=0.01)

    def test_ocr_only_uses_ocr_box(self):
        ocr = (300.0, 400.0, 500.0, 440.0)
        d = _divergence(DivergenceType.OCR_ONLY, ocr_bbox=ocr)
        report = _report_with_divergences([d])
        core = _core_finding()
        sr = _stage_result(report, findings=[core])

        result = _finding_bbox(sr, core, 0)
        assert result is not None
        assert result.x0 == pytest.approx(300 / 1200, abs=0.01)
        assert result.x1 == pytest.approx(500 / 1200, abs=0.01)

    def test_known_region_normalized_correctly(self):
        # Known pixel region: (120, 160, 360, 200) on a 1200×1600 page
        emb = (120.0, 160.0, 360.0, 200.0)
        d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[emb])
        report = _report_with_divergences([d], page_dims_px=((1200.0, 1600.0),))
        core = _core_finding()
        sr = _stage_result(report, findings=[core])

        result = _finding_bbox(sr, core, 0)
        assert result is not None
        assert result.x0 == pytest.approx(120 / 1200, abs=0.02)
        assert result.y0 == pytest.approx(160 / 1600, abs=0.02)
        assert result.x1 == pytest.approx(360 / 1200, abs=0.02)
        assert result.y1 == pytest.approx(200 / 1600, abs=0.02)


# ---------------------------------------------------------------------------
# AGREE-filter alignment (the critical invariant)
# ---------------------------------------------------------------------------

class TestOCRAGREEFilterAlignment:
    def test_agree_divergence_is_skipped(self):
        # List: [MISMATCH, AGREE, OCR_ONLY]
        # After filter: [MISMATCH, OCR_ONLY] — 2 findings
        mismatch = _divergence(
            DivergenceType.MISMATCH,
            embedded_bboxes=[(10.0, 20.0, 50.0, 40.0)],
            ocr_bbox=(8.0, 18.0, 52.0, 42.0),
            page_index=0,
        )
        agree = _divergence(DivergenceType.AGREE, embedded_bboxes=[(100.0, 200.0, 150.0, 220.0)])
        ocr_only = _divergence(
            DivergenceType.OCR_ONLY, ocr_bbox=(300.0, 400.0, 500.0, 440.0), page_index=0
        )
        report = _report_with_divergences([mismatch, agree, ocr_only])
        core0 = _core_finding(page=0)  # maps to MISMATCH
        core1 = _core_finding(page=0)  # maps to OCR_ONLY (AGREE was skipped)
        sr = _stage_result(report, findings=[core0, core1])

        result0 = _finding_bbox(sr, core0, 0)
        result1 = _finding_bbox(sr, core1, 1)

        assert result0 is not None
        # result0 should be the MISMATCH union: min(8,10)=8, min(18,20)=18, max(50,52)=52, max(40,42)=42
        assert result0.x0 == pytest.approx(8 / 1200, abs=0.01)

        assert result1 is not None
        # result1 should be the OCR_ONLY box: 300/1200
        assert result1.x0 == pytest.approx(300 / 1200, abs=0.01)

    def test_agree_divergence_does_not_get_a_bbox(self):
        # Requesting bbox for an index that would land on the AGREE (skipped) has
        # no meaning — but the AGREE is never in the findings list. Test that
        # indexing past the end of the filtered list returns None.
        agree = _divergence(DivergenceType.AGREE, embedded_bboxes=[(100.0, 200.0, 150.0, 220.0)])
        report = _report_with_divergences([agree])
        core = _core_finding()
        sr = _stage_result(report, findings=[])  # no findings (AGREE filtered out)
        assert _finding_bbox(sr, core, 0) is None


# ---------------------------------------------------------------------------
# Degradation + guard
# ---------------------------------------------------------------------------

class TestOCRFindingBboxDegradation:
    def test_missing_page_dims_px_returns_none(self):
        emb = (120.0, 160.0, 250.0, 200.0)
        d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[emb])
        report = _report_with_divergences([d], page_dims_px=())  # no dims
        core = _core_finding()
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_page_guard_mismatch_returns_none(self):
        emb = (120.0, 160.0, 250.0, 200.0)
        d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[emb], page_index=0)
        report = _report_with_divergences([d])
        core = _core_finding(page=3)  # page disagrees
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_high_value_guard_mismatch_returns_none(self):
        emb = (120.0, 160.0, 250.0, 200.0)
        d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[emb], token_class=TokenClass.AMOUNT)
        report = _report_with_divergences([d])
        core = _core_finding(high_value="date")  # divergence is AMOUNT, not DATE
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_no_usable_box_returns_none(self):
        # EMBEDDED_ONLY with no embedded words — degenerate case
        d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[])
        # Also no ocr box
        report = _report_with_divergences([d])
        core = _core_finding()
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_result_always_in_unit_range(self):
        emb = (-10.0, -20.0, 1500.0, 2000.0)  # off-page
        d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[emb])
        report = _report_with_divergences([d])
        core = _core_finding()
        sr = _stage_result(report, findings=[core])
        result = _finding_bbox(sr, core, 0)
        assert result is not None
        for v in (result.x0, result.y0, result.x1, result.y1):
            assert 0.0 <= v <= 1.0

    def test_no_payload_returns_none(self):
        sr = StageResult(
            stage="ocr_crosscheck", tier=ConfidenceTier.MEDIUM, score=50,
            findings=(), summary="", reasons=(), notes=(), ok=True, payload=None,
        )
        core = _core_finding()
        assert _finding_bbox(sr, core, 0) is None


# ---------------------------------------------------------------------------
# Via aggregate() end-to-end
# ---------------------------------------------------------------------------

def test_aggregate_ocr_finding_bbox_populated():
    emb = (120.0, 160.0, 360.0, 200.0)
    d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[emb])
    report = _report_with_divergences([d], page_dims_px=((1200.0, 1600.0),))
    core = _core_finding(page=0)
    sr = _stage_result(report, findings=[core])

    agg = aggregate([sr])
    ocr_findings = [f for f in agg.findings if f.stage == "ocr_crosscheck"]
    assert len(ocr_findings) == 1
    bbox = ocr_findings[0].bbox
    assert bbox is not None
    assert bbox.x0 == pytest.approx(120 / 1200, abs=0.02)
    assert bbox.y0 < bbox.y1


def test_aggregate_ocr_finding_bbox_none_without_dims():
    emb = (120.0, 160.0, 360.0, 200.0)
    d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[emb])
    report = _report_with_divergences([d], page_dims_px=())  # no dims
    core = _core_finding()
    sr = _stage_result(report, findings=[core])

    agg = aggregate([sr])
    assert agg.findings[0].bbox is None


# ---------------------------------------------------------------------------
# High-value token class guard mapping
# ---------------------------------------------------------------------------

class TestOCRHighValueMapping:
    def test_amount_token_class_maps_to_amount_high_value(self):
        emb = (120.0, 160.0, 360.0, 200.0)
        d = _divergence(DivergenceType.MISMATCH, embedded_bboxes=[emb],
                        ocr_bbox=(120.0, 160.0, 360.0, 200.0), token_class=TokenClass.AMOUNT)
        report = _report_with_divergences([d])
        core = _core_finding(high_value="amount")  # correct
        sr = _stage_result(report, findings=[core])
        result = _finding_bbox(sr, core, 0)
        assert result is not None

    def test_prose_token_class_maps_to_none_high_value(self):
        emb = (120.0, 160.0, 360.0, 200.0)
        d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[emb], token_class=TokenClass.PROSE)
        report = _report_with_divergences([d])
        core = _core_finding(high_value=None)  # None for prose
        sr = _stage_result(report, findings=[core])
        result = _finding_bbox(sr, core, 0)
        assert result is not None

    def test_prose_vs_amount_guard_fires(self):
        emb = (120.0, 160.0, 360.0, 200.0)
        d = _divergence(DivergenceType.EMBEDDED_ONLY, embedded_bboxes=[emb], token_class=TokenClass.PROSE)
        report = _report_with_divergences([d])
        core = _core_finding(high_value="amount")  # wrong: prose divergence, amount finding
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None
