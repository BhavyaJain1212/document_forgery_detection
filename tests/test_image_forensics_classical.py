"""Stage 6 — Session 6.4 acceptance tests: the REAL classical pixel DSP.

These drive the default :class:`ClassicalProvider` (ELA, double-JPEG/DQ,
JPEG-grid, noise-residual, copy-move) over real-pixel fixtures end-to-end through
``detect`` → ``score``. They are the completion proof that Stage 6 detects real
forgeries instead of returning INCONCLUSIVE-everywhere.

Real maps are noisy, so a flagged region is asserted to OVERLAP the ground-truth
patch (not be pixel-exact), per the design. The precision fixtures (clean scan,
whole-page recompression) must NOT produce a false HIGH/MEDIUM. All tests skip
gracefully when the classical CPU stack (numpy / cv2 / Pillow / scipy) is absent.
"""

from __future__ import annotations

import pytest

import make_image_forensics_fixtures as fixtures

from pdf_forgery.core.context import AnalysisContext
from pdf_forgery.core.types import ConfidenceTier
from pdf_forgery.image_forensics import (
    ClassicalProvider,
    ImageForensicsConfig,
    ImageForensicsStage,
)
from pdf_forgery.image_forensics.detect import detect

CFG = ImageForensicsConfig()
_W, _H = 400, 560

pytestmark = pytest.mark.skipif(
    not ClassicalProvider().is_available(),
    reason="classical CPU stack (numpy/cv2/Pillow/scipy) unavailable",
)


def _run(pdf: bytes):
    ctx = AnalysisContext(pdf)
    res = ImageForensicsStage().run(pdf, ctx)
    assert res.ok is True
    return res


def _overlaps(region_bbox, gt_frac) -> bool:
    x0, top, x1, bottom = region_bbox
    gx0, gtop, gx1, gbot = (
        gt_frac[0] * _W, gt_frac[1] * _H, gt_frac[2] * _W, gt_frac[3] * _H,
    )
    return min(x1, gx1) > max(x0, gx0) and min(bottom, gbot) > max(top, gtop)


# --------------------------------------------------------------------------- #
# Recall — the positives
# --------------------------------------------------------------------------- #

def test_spliced_amount_is_high_co_located_over_region():
    pdf, gt = fixtures.build_spliced_amount_pdf(_W, _H)
    res = _run(pdf)
    assert res.tier is ConfidenceTier.HIGH
    assert res.score >= 70
    # At least one co-located (≥2 distinct methods) region overlaps the patch.
    regions = res.payload.detection.regions
    co = [r for r in regions if r.co_located and r.page_bbox]
    assert co, "expected a co-located region"
    assert any(_overlaps(r.page_bbox, gt) for r in co)
    assert any(r.high_value for r in co)


def test_double_compressed_region_is_localized():
    # A LOCAL re-compression break is localized by the JPEG methods (≥ MEDIUM).
    pdf, gt = fixtures.build_double_compressed_pdf(_W, _H)
    res = _run(pdf)
    assert res.tier in (ConfidenceTier.MEDIUM, ConfidenceTier.HIGH)
    located = [r for r in res.payload.detection.regions if r.page_bbox]
    assert any(_overlaps(r.page_bbox, gt) for r in located)


def test_copy_move_duplicate_is_detected():
    pdf, gt = fixtures.build_copy_move_pdf(_W, _H)
    res = _run(pdf)
    assert res.tier in (ConfidenceTier.MEDIUM, ConfidenceTier.HIGH)
    # The copy-move method backs a region over the duplicate, and it is reported.
    methods = {m for r in res.payload.detection.regions for m in r.methods}
    assert "copy_move" in methods
    located = [r for r in res.payload.detection.regions if r.page_bbox]
    assert any(_overlaps(r.page_bbox, gt) for r in located)


# --------------------------------------------------------------------------- #
# Precision — the negatives (must NOT false-fire)
# --------------------------------------------------------------------------- #

def test_clean_scan_is_low_no_region():
    res = _run(fixtures.build_clean_scan_pdf(_W, _H))
    assert res.tier is ConfidenceTier.LOW
    assert res.findings == ()
    assert res.payload.detection.regions == ()


def test_whole_page_recompression_has_no_local_region():
    # An innocent global rescan: globally double-quantised but NO local break →
    # the global-suppression / robust-z anomaly maps yield no localized region.
    res = _run(fixtures.build_recompressed_pdf(_W, _H))
    assert res.tier in (ConfidenceTier.LOW, ConfidenceTier.INCONCLUSIVE)
    assert res.payload.detection.regions == ()
    # The stage analysed (image-dominant) but found nothing localized.
    assert res.payload.detection.executions >= 1


def test_clean_scan_runs_real_methods_no_capability_gap():
    # The DSP is implemented: a real run executes methods, never reports a gap.
    res = _run(fixtures.build_clean_scan_pdf(_W, _H))
    d = res.payload.detection
    assert d.capability_gaps == ()
    assert d.method_errors == ()
    assert d.executions >= 1


def test_detect_is_read_only_and_never_raises_on_real_pixels():
    pdf, _ = fixtures.build_spliced_amount_pdf(_W, _H)
    ctx = AnalysisContext(pdf)
    # Two runs over the same context are identical (cached decode, deterministic).
    a = detect(ctx, provider=ClassicalProvider(), config=CFG)
    b = detect(ctx, provider=ClassicalProvider(), config=CFG)
    assert len(a.regions) == len(b.regions)
    assert a.executions == b.executions
