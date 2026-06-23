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

from dataclasses import replace
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

import make_image_forensics_fixtures as fixtures

from pdf_forgery.core.context import AnalysisContext
from pdf_forgery.core.types import ConfidenceTier
from pdf_forgery.image_forensics import (
    ClassicalProvider,
    ImageForensicsConfig,
    ImageForensicsStage,
)
from pdf_forgery.image_forensics.detect import detect
from pdf_forgery.image_forensics.classical import (
    copy_move_heatmap,
    double_jpeg_heatmap,
    ela_heatmap,
    noise_heatmap,
)
from pdf_forgery.image_forensics.images import DecodedImage, extract_images

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


def _decoded(pixels: np.ndarray, *, content_hash: str = "12345678") -> DecodedImage:
    h, w = pixels.shape[:2]
    return DecodedImage(
        page_index=0,
        xobject_id="1 0",
        colorspace="/DeviceRGB",
        filters=(),
        width=w,
        height=h,
        bits=8,
        has_smask=False,
        content_hash=content_hash,
        pixels=pixels,
        page_width_pt=float(w),
        page_height_pt=float(h),
    )


def _decoded_jpeg(pixels: np.ndarray, *, quality: int = 90) -> DecodedImage:
    """Round-trip pixels through JPEG and retain the verbatim source stream."""
    buf = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(buf, format="JPEG", quality=quality)
    jpeg = buf.getvalue()
    decoded = np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))
    image = _decoded(decoded)
    return replace(image, filters=("/DCTDecode",), jpeg_bytes=jpeg)


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


# --------------------------------------------------------------------------- #
# Document-aware precision gates
# --------------------------------------------------------------------------- #

def test_ela_structure_gate_suppresses_text_and_rule_edges():
    rng = np.random.default_rng(33)
    pixels = np.clip(
        242 + rng.normal(0, 1.3, (_H, _W, 3)), 0, 255
    ).astype(np.uint8)
    pil = Image.fromarray(pixels, "RGB")
    draw = ImageDraw.Draw(pil)
    for y in range(16, _H - 16, 24):
        draw.line((8, y, _W - 8, y), fill=(25, 25, 25), width=1)
        for x in range(12, _W - 20, 38):
            draw.rectangle((x, y - 9, x + 18, y - 5), fill=(35, 35, 35))
    for x in range(8, _W - 7, 64):
        draw.line((x, 8, x, _H - 8), fill=(60, 60, 60), width=1)

    image = _decoded(np.asarray(pil))
    # Lower z0 exposes the ordinary edge response this unit is isolating; the
    # production threshold remains unchanged and is covered end-to-end below.
    cfg = replace(CFG, anomaly_z0=0.5)
    legacy = ela_heatmap(image, replace(cfg, ela_structure_gate=False))
    gated = ela_heatmap(image, replace(cfg, ela_structure_gate=True))

    assert legacy is not None and gated is not None
    legacy_hot = int((legacy >= cfg.ela_threshold).sum())
    gated_hot = int((gated >= cfg.ela_threshold).sum())
    assert legacy_hot > 0
    assert gated_hot < legacy_hot * 0.75


def test_ela_structure_gate_keeps_flat_foreign_patch():
    pdf, gt = fixtures.build_spliced_amount_pdf(_W, _H)
    image = extract_images(pdf)[0]
    hm = ela_heatmap(image, CFG)
    assert hm is not None
    rows, cols = hm.shape
    patch = hm[
        int(gt[1] * rows) : max(int(gt[3] * rows), int(gt[1] * rows) + 1),
        int(gt[0] * cols) : max(int(gt[2] * cols), int(gt[0] * cols) + 1),
    ]
    assert patch.size
    assert float(patch.max()) >= CFG.ela_threshold


def test_dq_structure_gate_suppresses_text_and_rule_edges():
    rng = np.random.default_rng(55)
    pixels = np.clip(
        244 + rng.normal(0, 0.8, (_H, _W, 3)), 0, 255
    ).astype(np.uint8)
    pil = Image.fromarray(pixels, "RGB")
    draw = ImageDraw.Draw(pil)
    for y in range(12, _H - 12, 18):
        draw.line((5, y, _W - 5, y), fill=(25, 25, 25), width=1)
        for x in range(10, _W - 24, 31):
            draw.rectangle((x, y - 7, x + 16, y - 3), fill=(40, 40, 40))

    image = _decoded_jpeg(np.asarray(pil))
    legacy = double_jpeg_heatmap(image, replace(CFG, dq_structure_gate=False))
    gated = double_jpeg_heatmap(image, CFG)

    assert legacy is not None and gated is not None
    legacy_hot = int((legacy >= CFG.dq_threshold).sum())
    gated_hot = int((gated >= CFG.dq_threshold).sum())
    assert legacy_hot > 0
    assert gated_hot < legacy_hot * 0.50


def test_noise_retune_keeps_foreign_noise_patch_above_threshold():
    pdf, gt = fixtures.build_spliced_amount_pdf(_W, _H)
    image = extract_images(pdf)[0]
    hm = noise_heatmap(image, CFG)
    assert hm is not None
    rows, cols = hm.shape
    patch = hm[
        int(gt[1] * rows) : max(int(gt[3] * rows), int(gt[1] * rows) + 1),
        int(gt[0] * cols) : max(int(gt[2] * cols), int(gt[0] * cols) + 1),
    ]
    assert patch.size
    assert float(patch.max()) >= CFG.noise_threshold


def test_copy_move_suppresses_structural_half_page_duplicate():
    rng = np.random.default_rng(44)
    top_copy = rng.integers(0, 256, size=(_H // 2, _W, 3), dtype=np.uint8)
    image = _decoded(np.vstack([top_copy, top_copy]), content_hash="abcdef12")

    legacy = copy_move_heatmap(
        image, replace(CFG, copy_move_max_cluster_span_frac=1.0)
    )
    gated = copy_move_heatmap(image, CFG)
    assert legacy is not None, "fixture must exercise the structural span guard"
    assert gated is None


def test_clean_multicopy_w2_has_no_dq_or_noise_review_finding():
    from pdf_forgery.aggregate.server import _wrap_image_as_pdf

    path = (
        Path(__file__).resolve().parents[1]
        / "test_files"
        / "W2_XL_input_clean_1000.jpg"
    )
    if not path.exists():
        pytest.skip("real clean W-2 precision baseline is absent")
    pdf = _wrap_image_as_pdf(path.read_bytes())
    res = _run(pdf)

    assert res.tier in (ConfidenceTier.LOW, ConfidenceTier.INCONCLUSIVE)
    assert not any(
        finding.tier is ConfidenceTier.MEDIUM
        and set(finding.region.methods) & {"double_jpeg", "noise_inconsistency"}
        for finding in res.payload.findings
    )
    assert not any(
        set(region.methods) & {"double_jpeg", "noise_inconsistency"}
        for region in res.payload.detection.regions
    )


# --------------------------------------------------------------------------- #
# Localization through the aggregate layer (image_forensics bbox wiring)
# --------------------------------------------------------------------------- #

def _norm_overlaps_frac(bbox, gt_frac) -> bool:
    """Normalized [0,1] ``BBox`` overlaps the fractional top-left GT patch."""
    return (
        min(bbox.x1, gt_frac[2]) > max(bbox.x0, gt_frac[0])
        and min(bbox.y1, gt_frac[3]) > max(bbox.y0, gt_frac[1])
    )


def test_spliced_amount_localizes_through_aggregate():
    from pdf_forgery.aggregate import aggregate

    pdf, gt = fixtures.build_spliced_amount_pdf(_W, _H)
    res = _run(pdf)
    agg = aggregate([res])
    boxes = [f.bbox for f in agg.findings if f.bbox is not None]
    assert boxes, "expected at least one localized image_forensics finding"
    assert any(_norm_overlaps_frac(b, gt) for b in boxes)


def test_wrapped_image_upload_localizes_through_aggregate():
    # A raw image upload is wrapped into a full-page PDF by the server; the
    # verbatim JPEG keeps the splice signal, and the full-page placement makes
    # localization the simplest case of the same method.
    from pdf_forgery.aggregate import aggregate
    from pdf_forgery.aggregate.server import _wrap_image_as_pdf
    from pdf_forgery.image_forensics.images import extract_images

    spliced_pdf, gt = fixtures.build_spliced_amount_pdf(_W, _H)
    jpeg = next(i.jpeg_bytes for i in extract_images(spliced_pdf) if i.jpeg_bytes)
    wrapped = _wrap_image_as_pdf(jpeg)

    res = _run(wrapped)
    agg = aggregate([res])
    boxes = [f.bbox for f in agg.findings if f.bbox is not None]
    assert boxes, "wrapped image upload should localize (full-page placement)"
    assert any(_norm_overlaps_frac(b, gt) for b in boxes)
