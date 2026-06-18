"""Tests for font_forensics bounding-box localization.

Verifies that:
  - FontReport carries page_dims / page_rotations
  - aggregate()._finding_bbox correctly normalizes token and suspicious-glyph bboxes
  - INTRA_TOKEN_FONT_MIX uses suspicious_bboxes union (tighter); other kinds use token bbox
  - Degradation, rotation, and positional guard work correctly
"""

from __future__ import annotations

import pytest

from pdf_forgery.aggregate import aggregate
from pdf_forgery.aggregate.aggregate import _finding_bbox
from pdf_forgery.aggregate.models import BBox
from pdf_forgery.core.types import ConfidenceTier, Finding, StageResult
from pdf_forgery.font_forensics.models import (
    FontFinding,
    FontFindingKind,
    FontReport,
)
from pdf_forgery.revision_recovery.models import HighValueKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _font_finding(
    page_index: int = 0,
    kind: FontFindingKind = FontFindingKind.WHOLE_TOKEN_FAMILY_DIFFERENCE,
    bbox: tuple[float, float, float, float] = (100.0, 200.0, 250.0, 220.0),
    suspicious_bboxes: tuple = (),
    high_value=None,
) -> FontFinding:
    return FontFinding(
        page_index=page_index,
        kind=kind,
        tier=ConfidenceTier.MEDIUM,
        token="test",
        token_font="ABCDEF+Helvetica",
        context_font="XYZABC+Helvetica",
        bbox=bbox,
        reason="test",
        high_value=high_value,
        suspicious_bboxes=suspicious_bboxes,
    )


def _core_finding(page: int = 0, high_value: str | None = None) -> Finding:
    return Finding(
        stage="font_forensics",
        tier=ConfidenceTier.MEDIUM,
        reason="test",
        page=page,
        high_value=high_value,
    )


def _report(findings, page_dims=(), page_rotations=()) -> FontReport:
    return FontReport(
        path="<test>",
        ok=True,
        tier=ConfidenceTier.MEDIUM,
        score=55,
        findings=tuple(findings),
        page_dims=page_dims,
        page_rotations=page_rotations,
    )


def _stage_result(report: FontReport, findings=()) -> StageResult:
    return StageResult(
        stage="font_forensics",
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
# Whole-token finding bbox
# ---------------------------------------------------------------------------

class TestFontFindingBboxWholeToken:
    def test_known_position_normalized_correctly(self):
        # Cell at PDF bottom-left space (100, 200, 250, 220); page 612×792
        rf = _font_finding(bbox=(100.0, 200.0, 250.0, 220.0))
        report = _report([rf], page_dims=((612.0, 792.0),), page_rotations=(0,))
        core = _core_finding(page=0)
        sr = _stage_result(report, findings=[core])

        result = _finding_bbox(sr, core, 0)
        assert result is not None
        assert isinstance(result, BBox)
        assert result.x0 == pytest.approx(100 / 612, abs=0.02)
        assert result.y0 == pytest.approx((792 - 220) / 792, abs=0.02)
        assert result.x1 == pytest.approx(250 / 612, abs=0.02)
        assert result.y1 == pytest.approx((792 - 200) / 792, abs=0.02)
        assert result.y0 < result.y1  # top-left orientation

    def test_sentinel_bbox_returns_none(self):
        rf = _font_finding(bbox=(0.0, 0.0, 0.0, 0.0))
        report = _report([rf], page_dims=((612.0, 792.0),))
        core = _core_finding()
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_missing_page_dims_returns_none(self):
        rf = _font_finding(bbox=(100.0, 200.0, 250.0, 220.0))
        report = _report([rf])  # no dims
        core = _core_finding()
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None


# ---------------------------------------------------------------------------
# INTRA_TOKEN_FONT_MIX: suspicious_bboxes union (tighter than token)
# ---------------------------------------------------------------------------

class TestFontFindingBboxIntraToken:
    def test_suspicious_bboxes_used_when_available(self):
        # Suspicious glyph: a narrow sliver within a wider token
        # token bbox:      (100, 200, 250, 220)
        # suspicious bbox: (170, 202, 190, 218)  -- tighter
        rf = _font_finding(
            kind=FontFindingKind.INTRA_TOKEN_FONT_MIX,
            bbox=(100.0, 200.0, 250.0, 220.0),
            suspicious_bboxes=((170.0, 202.0, 190.0, 218.0),),
        )
        report = _report([rf], page_dims=((612.0, 792.0),), page_rotations=(0,))
        core = _core_finding()
        sr = _stage_result(report, findings=[core])

        result = _finding_bbox(sr, core, 0)
        assert result is not None
        # Should use suspicious_bboxes: x0=170/612
        assert result.x0 == pytest.approx(170 / 612, abs=0.02)
        assert result.x1 == pytest.approx(190 / 612, abs=0.02)
        # Box is tighter than the full token (100/612 → 250/612)
        assert result.x0 > 100 / 612

    def test_suspicious_bboxes_union_of_multiple(self):
        # Two suspicious glyphs: union their bboxes
        rf = _font_finding(
            kind=FontFindingKind.INTRA_TOKEN_FONT_MIX,
            bbox=(100.0, 200.0, 250.0, 220.0),
            suspicious_bboxes=(
                (120.0, 202.0, 140.0, 218.0),
                (180.0, 202.0, 200.0, 218.0),
            ),
        )
        report = _report([rf], page_dims=((612.0, 792.0),), page_rotations=(0,))
        core = _core_finding()
        sr = _stage_result(report, findings=[core])

        result = _finding_bbox(sr, core, 0)
        assert result is not None
        assert result.x0 == pytest.approx(120 / 612, abs=0.02)
        assert result.x1 == pytest.approx(200 / 612, abs=0.02)

    def test_empty_suspicious_bboxes_falls_back_to_token(self):
        rf = _font_finding(
            kind=FontFindingKind.INTRA_TOKEN_FONT_MIX,
            bbox=(100.0, 200.0, 250.0, 220.0),
            suspicious_bboxes=(),  # empty
        )
        report = _report([rf], page_dims=((612.0, 792.0),), page_rotations=(0,))
        core = _core_finding()
        sr = _stage_result(report, findings=[core])

        result = _finding_bbox(sr, core, 0)
        assert result is not None
        # Falls back to token bbox
        assert result.x0 == pytest.approx(100 / 612, abs=0.02)


# ---------------------------------------------------------------------------
# Degradation + guard
# ---------------------------------------------------------------------------

class TestFontFindingBboxDegradation:
    def test_page_guard_mismatch_returns_none(self):
        rf = _font_finding(page_index=0, bbox=(100.0, 200.0, 250.0, 220.0))
        report = _report([rf], page_dims=((612.0, 792.0),))
        core = _core_finding(page=5)  # disagrees
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_high_value_guard_mismatch_returns_none(self):
        rf = _font_finding(bbox=(100.0, 200.0, 250.0, 220.0), high_value=HighValueKind.AMOUNT)
        report = _report([rf], page_dims=((612.0, 792.0),))
        core = _core_finding(page=0, high_value="date")  # wrong
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 0) is None

    def test_rotation_90_stays_in_unit_range(self):
        rf = _font_finding(bbox=(100.0, 200.0, 250.0, 220.0))
        report = _report([rf], page_dims=((612.0, 792.0),), page_rotations=(90,))
        core = _core_finding()
        sr = _stage_result(report, findings=[core])
        result = _finding_bbox(sr, core, 0)
        assert result is not None
        for v in (result.x0, result.y0, result.x1, result.y1):
            assert 0.0 <= v <= 1.0

    def test_index_out_of_range_returns_none(self):
        rf = _font_finding()
        report = _report([rf], page_dims=((612.0, 792.0),))
        core = _core_finding()
        sr = _stage_result(report, findings=[core])
        assert _finding_bbox(sr, core, 99) is None  # index 99 out of range


# ---------------------------------------------------------------------------
# Via aggregate() end-to-end
# ---------------------------------------------------------------------------

def test_aggregate_font_finding_bbox_populated():
    rf = _font_finding(bbox=(100.0, 200.0, 250.0, 220.0))
    report = _report([rf], page_dims=((612.0, 792.0),), page_rotations=(0,))
    core = _core_finding(page=0)
    sr = _stage_result(report, findings=[core])

    agg = aggregate([sr])
    font_findings = [f for f in agg.findings if f.stage == "font_forensics"]
    assert len(font_findings) == 1
    bbox = font_findings[0].bbox
    assert bbox is not None
    assert bbox.x0 == pytest.approx(100 / 612, abs=0.02)
    assert bbox.y0 < bbox.y1


def test_aggregate_font_finding_bbox_none_without_dims():
    rf = _font_finding(bbox=(100.0, 200.0, 250.0, 220.0))
    report = _report([rf])  # no dims
    core = _core_finding()
    sr = _stage_result(report, findings=[core])

    agg = aggregate([sr])
    assert agg.findings[0].bbox is None


# ---------------------------------------------------------------------------
# FontReport carries dims after analyze_bytes
# ---------------------------------------------------------------------------

def test_analyze_bytes_populates_page_dims(font_forged_pdf):
    from pdf_forgery.font_forensics import analyze_path
    report = analyze_path(font_forged_pdf)
    assert report.ok is True
    assert len(report.page_dims) > 0
    for w, h in report.page_dims:
        assert w > 0
        assert h > 0


def test_analyze_bytes_localization_disabled_returns_empty_dims():
    from pdf_forgery.font_forensics import analyze_bytes
    from pdf_forgery.font_forensics.config import FontConfig

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

    cfg = FontConfig(enable_localization=False)
    report = analyze_bytes(raw, config=cfg)
    assert report.page_dims == ()
    assert report.page_rotations == ()
