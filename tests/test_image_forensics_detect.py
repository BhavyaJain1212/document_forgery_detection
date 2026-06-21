"""Stage 6 — Session 6.2 tests: detectors (combination) + bbox localization.

Per the design (§ "Test fixtures"), the detect/localize LOGIC is driven by a
CONTROLLABLE provider emitting known heatmaps over known regions — deterministic,
no real DSP (the classical pixel math is a separate follow-up). Covers:

* ``localize.py`` — heatmap → blobs, the pixel-grid → page-point transform
  (hand-checked against known corners), high-value band tagging, IoU.
* ``detect.py``   — global/whole-image suppression, speckle drop, co-located
  corroboration, method-error capture, and the end-to-end ``detect()`` path over
  a real image-dominant fixture (spliced-amount → localized high-value region;
  clean / whole-page recompression → no surviving region).
"""

from __future__ import annotations

import numpy as np
import pytest

import make_fixtures  # scripts/ is on sys.path via conftest
import make_image_forensics_fixtures as fixtures

from pdf_forgery.core.context import AnalysisContext
from pdf_forgery.image_forensics import (
    ClassicalProvider,
    DecodedImage,
    ImageForensicsConfig,
    combine_fires,
    detect,
)
from pdf_forgery.image_forensics.detect import MethodFire, _fires_from_map
from pdf_forgery.image_forensics.engine import ForensicMap, ForensicProvenance
from pdf_forgery.image_forensics.localize import (
    Blob,
    blob_to_page_bbox,
    frac_bbox_to_page_bbox,
    heatmap_blobs,
    hot_fraction,
    iou,
    overlaps_high_value,
)

CFG = ImageForensicsConfig()


# --------------------------------------------------------------------------- #
# Controllable test provider — emits canned heatmaps (no signal processing)
# --------------------------------------------------------------------------- #

class _CannedMethod:
    def __init__(self, name, *, heatmap=None, scalar=None, requires_jpeg=False, raises=None):
        self.name = name
        self.version = "canned-1"
        self._heatmap = heatmap
        self._scalar = scalar
        self._requires_jpeg = requires_jpeg
        self._raises = raises

    def applicable(self, image, cfg):
        return (not self._requires_jpeg) or image.is_jpeg

    def analyze(self, image, cfg):
        if self._raises is not None:
            raise self._raises
        return ForensicMap(
            method=self.name, version=self.version, heatmap=self._heatmap, scalar=self._scalar
        )


class _CannedProvider:
    name = "canned"
    device = "cpu"

    def __init__(self, methods):
        self._methods = methods

    def is_available(self):
        return True

    def methods(self, cfg):
        return self._methods

    def provenance(self, cfg):
        return ForensicProvenance(provider=self.name, device=self.device)


def _heatmap(hot_box=None, *, value=0.9, base=0.0, shape=(100, 100)):
    arr = np.full(shape, base, dtype=np.float64)
    if hot_box is not None:
        r0, c0, r1, c1 = hot_box
        arr[r0:r1, c0:c1] = value
    return arr


def _fake_image(*, placement=(0, 0, 64, 48), page=(64, 48), jpeg=True):
    return DecodedImage(
        page_index=0,
        xobject_id="1 0",
        colorspace="/DeviceRGB",
        filters=("/DCTDecode",) if jpeg else ("/FlateDecode",),
        width=64,
        height=48,
        bits=8,
        has_smask=False,
        content_hash="abc",
        pixels=np.zeros((48, 64, 3), dtype=np.uint8),
        jpeg_bytes=b"\xff\xd8" if jpeg else None,
        placement=placement,
        page_width_pt=page[0],
        page_height_pt=page[1],
    )


def _fire(bbox, method, *, page=0, strength=0.9, page_h=48.0):
    return MethodFire(
        page_index=page,
        method=method,
        version="v",
        page_bbox=bbox,
        area_frac=0.04,
        strength=strength,
        page_width_pt=64.0,
        page_height_pt=page_h,
    )


# --------------------------------------------------------------------------- #
# localize.py — coordinate transform (hand-checked)
# --------------------------------------------------------------------------- #

class TestLocalizeTransform:
    def test_frac_bbox_to_page_bbox_known_points(self):
        # Placement (x0=100, top0=200, x1=300, top1=600): 200pt wide, 400pt tall.
        img = _fake_image(placement=(100, 200, 300, 600), page=(400, 800))
        # The lower-right quadrant of the image grid.
        out = frac_bbox_to_page_bbox((0.5, 0.0, 1.0, 0.5), img)
        # x: 100 + 0.5*200 = 200 .. 100 + 1.0*200 = 300
        # top: 200 + 0.0*400 = 200 .. 200 + 0.5*400 = 400
        assert out == pytest.approx((200.0, 200.0, 300.0, 400.0))

    def test_blob_to_page_bbox_full_image(self):
        img = _fake_image(placement=(0, 0, 64, 48))
        out = blob_to_page_bbox(Blob(0.0, 0.0, 1.0, 1.0, 1.0, 0.9), img)
        assert out == pytest.approx((0.0, 0.0, 64.0, 48.0))

    def test_no_placement_returns_none(self):
        img = _fake_image(placement=None)
        assert blob_to_page_bbox(Blob(0, 0, 1, 1, 1.0, 0.9), img) is None


# --------------------------------------------------------------------------- #
# localize.py — heatmap → blobs
# --------------------------------------------------------------------------- #

class TestHeatmapBlobs:
    def test_localizes_hot_rectangle(self):
        hm = _heatmap((60, 40, 80, 60))  # rows 60:80, cols 40:60 of a 100x100 grid
        blobs = heatmap_blobs(hm, threshold=0.5, min_area_frac=0.005)
        assert len(blobs) == 1
        b = blobs[0]
        assert (b.u0, b.v0, b.u1, b.v1) == pytest.approx((0.40, 0.60, 0.60, 0.80))
        assert b.peak == pytest.approx(0.9)

    def test_speckle_below_min_area_is_dropped(self):
        hm = _heatmap((10, 10, 11, 11))  # 1px = 0.0001 of a 100x100 grid
        assert heatmap_blobs(hm, threshold=0.5, min_area_frac=0.005) == []

    def test_two_separate_blobs(self):
        hm = _heatmap((5, 5, 15, 15))
        hm[70:85, 70:85] = 0.9
        blobs = heatmap_blobs(hm, threshold=0.5, min_area_frac=0.005)
        assert len(blobs) == 2

    def test_below_threshold_yields_nothing(self):
        assert heatmap_blobs(_heatmap(base=0.1), threshold=0.5, min_area_frac=0.005) == []

    def test_hot_fraction(self):
        hm = _heatmap((0, 0, 50, 100))  # top half hot
        assert hot_fraction(hm, 0.5) == pytest.approx(0.5)
        assert hot_fraction(_heatmap(base=0.1), 0.5) == 0.0


# --------------------------------------------------------------------------- #
# localize.py — geometry helpers
# --------------------------------------------------------------------------- #

class TestGeometry:
    def test_iou(self):
        assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
        assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
        # Half-overlap: intersection 50, union 150.
        assert iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(50 / 150)

    def test_high_value_band_default_lower_half(self):
        # page height 100; default band is lower half (0.5..1.0, top-down).
        assert overlaps_high_value((0, 60, 10, 80), 100.0, CFG)        # in band
        assert not overlaps_high_value((0, 10, 10, 30), 100.0, CFG)    # upper quarter
        assert not overlaps_high_value((0, 60, 10, 80), None, CFG)     # unknown height


# --------------------------------------------------------------------------- #
# detect.py — global / whole-image suppression
# --------------------------------------------------------------------------- #

class TestSuppression:
    def test_whole_image_lift_is_global_not_a_fire(self):
        # Entire heatmap hot → recompression / uniform artifact, not a local edit.
        fmap = ForensicMap(method="ela", version="v", heatmap=_heatmap(base=0.9))
        fires, globals_ = _fires_from_map(fmap, _fake_image(jpeg=False), CFG)
        assert fires == []
        assert len(globals_) == 1
        assert globals_[0].method == "ela"
        assert globals_[0].coverage >= CFG.global_coverage_frac

    def test_local_blob_is_a_fire(self):
        fmap = ForensicMap(method="noise_inconsistency", version="v",
                           heatmap=_heatmap((60, 40, 80, 60)))
        fires, globals_ = _fires_from_map(fmap, _fake_image(), CFG)
        assert len(fires) == 1
        assert globals_ == []
        assert fires[0].page_bbox is not None

    def test_scalar_only_firing_is_global(self):
        fmap = ForensicMap(method="jpeg_grid", version="v", heatmap=None, scalar=0.9)
        fires, globals_ = _fires_from_map(fmap, _fake_image(), CFG)
        assert fires == [] and len(globals_) == 1

    def test_scalar_below_threshold_is_silent(self):
        fmap = ForensicMap(method="jpeg_grid", version="v", heatmap=None, scalar=0.1)
        assert _fires_from_map(fmap, _fake_image(), CFG) == ([], [])


# --------------------------------------------------------------------------- #
# detect.py — corroboration / region combination
# --------------------------------------------------------------------------- #

class TestCombineFires:
    def test_two_methods_same_region_are_colocated(self):
        box = (10, 30, 30, 45)
        regions = combine_fires(
            [_fire(box, "double_jpeg"), _fire(box, "noise_inconsistency")], CFG
        )
        assert len(regions) == 1
        r = regions[0]
        assert r.co_located is True
        assert r.methods == ("double_jpeg", "noise_inconsistency")

    def test_single_method_is_not_colocated(self):
        regions = combine_fires([_fire((10, 30, 30, 45), "double_jpeg")], CFG)
        assert len(regions) == 1
        assert regions[0].co_located is False

    def test_same_method_twice_is_not_colocated(self):
        box = (10, 30, 30, 45)
        regions = combine_fires([_fire(box, "ela"), _fire(box, "ela")], CFG)
        assert len(regions) == 1
        assert regions[0].co_located is False
        assert regions[0].methods == ("ela",)

    def test_disjoint_fires_make_separate_regions(self):
        regions = combine_fires(
            [_fire((0, 0, 5, 5), "ela"), _fire((40, 40, 50, 50), "noise_inconsistency")],
            CFG,
        )
        assert len(regions) == 2

    def test_high_value_band_tag(self):
        # page height 48; band lower half → top0 >= 24.
        low = _fire((10, 30, 30, 45), "double_jpeg")        # in band
        high = _fire((10, 2, 30, 10), "double_jpeg")        # upper page
        assert combine_fires([low], CFG)[0].high_value is True
        assert combine_fires([high], CFG)[0].high_value is False


# --------------------------------------------------------------------------- #
# detect.py — end-to-end over real image-dominant fixtures
# --------------------------------------------------------------------------- #

class TestDetectEndToEnd:
    def test_spliced_amount_localizes_high_value_region(self):
        # A full-page scanned bill; a DQ ghost AND a co-located noise break over
        # the (lower) amount band — the known-positive shape.
        pdf, _ = fixtures.build_jpeg_image_pdf()
        ctx = AnalysisContext(pdf)
        hm = _heatmap((60, 40, 90, 70))  # lower-middle of the page grid
        prov = _CannedProvider([
            _CannedMethod("double_jpeg", heatmap=hm, requires_jpeg=True),
            _CannedMethod("noise_inconsistency", heatmap=hm),
        ])
        res = detect(ctx, provider=prov, config=CFG)
        assert res.analyzed_pages == (0,)
        assert len(res.regions) == 1
        region = res.regions[0]
        assert region.co_located is True
        assert region.high_value is True
        # The region sits in the lower band (altered area), not the whole page.
        assert region.page_bbox is not None
        _x0, top, _x1, bottom = region.page_bbox
        assert top > region.page_height_pt * CFG.high_value_band_top_frac
        assert bottom <= region.page_height_pt + 1e-6

    def test_clean_scan_has_no_surviving_region(self):
        pdf, _ = fixtures.build_jpeg_image_pdf()
        ctx = AnalysisContext(pdf)
        prov = _CannedProvider([
            _CannedMethod("noise_inconsistency", heatmap=_heatmap(base=0.1)),
        ])
        res = detect(ctx, provider=prov, config=CFG)
        assert res.regions == ()
        assert res.global_signals == ()

    def test_whole_page_recompression_no_local_region(self):
        pdf, _ = fixtures.build_jpeg_image_pdf()
        ctx = AnalysisContext(pdf)
        prov = _CannedProvider([
            _CannedMethod("ela", heatmap=_heatmap(base=0.95)),  # whole image lifts
        ])
        res = detect(ctx, provider=prov, config=CFG)
        assert res.regions == ()              # no LOCAL region
        assert len(res.global_signals) == 1   # recorded as diffuse, not dropped

    def test_method_error_is_recorded_never_crashes(self):
        pdf, _ = fixtures.build_jpeg_image_pdf()
        ctx = AnalysisContext(pdf)
        prov = _CannedProvider([
            _CannedMethod("double_jpeg", raises=RuntimeError("boom")),
        ])
        res = detect(ctx, provider=prov, config=CFG)
        assert res.regions == ()
        assert len(res.method_errors) == 1
        assert res.method_errors[0].method == "double_jpeg"
        assert res.method_errors[0].reason == "RuntimeError"

    def test_no_image_dominant_page_is_empty(self):
        # Digital-native page → stage contributes nothing (INCONCLUSIVE in 6.3).
        ctx = AnalysisContext(make_fixtures.build_clean())
        res = detect(ctx, provider=_CannedProvider([]), config=CFG)
        assert res.analyzed_pages == ()
        assert res.regions == ()

    def test_real_classical_provider_degrades_to_capability_gaps(self):
        # The classical methods still raise NotImplementedError (deferred DSP);
        # detect() must record those as CAPABILITY GAPS (not runtime errors) and
        # never crash. Gaps are "no signal" → INCONCLUSIVE in scoring, so the live
        # stage cannot manufacture a false MEDIUM while the DSP is deferred.
        if not ClassicalProvider().is_available():
            pytest.skip("classical CPU stack (numpy/cv2/PIL) unavailable")
        pdf, _ = fixtures.build_jpeg_image_pdf()
        ctx = AnalysisContext(pdf)
        res = detect(ctx, provider=ClassicalProvider(), config=CFG)
        assert res.regions == ()
        assert res.provider == "classical"
        assert res.method_errors == ()        # not runtime errors
        assert len(res.capability_gaps) >= 1  # deferred capability
        assert res.analyzed is False          # nothing actually executed
