"""Stage 6 — Session 6.3 tests: the §7 scoring rule tree.

Drives ``scoring.score(detection, activation, cfg)`` with hand-built
``DetectionResult`` / ``DocumentActivation`` objects (no pixels) to exercise every
tier branch: INCONCLUSIVE (digital-native / capability-gap-only), LOW (weak /
global), MEDIUM (lone region / method error), HIGH (co-located corroboration).
"""

from __future__ import annotations

from pdf_forgery.core.types import ConfidenceTier
from pdf_forgery.image_forensics import ImageForensicsConfig, score
from pdf_forgery.image_forensics.activation import DocumentActivation, PageActivation
from pdf_forgery.image_forensics.detect import (
    CapabilityGap,
    DetectionResult,
    GlobalSignal,
    MethodError,
    MethodFire,
    TamperRegion,
)

CFG = ImageForensicsConfig()


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #

def _activation(*, dominant=(0,), n=1):
    pages = tuple(
        PageActivation(
            page_index=i,
            embedded_words=0 if i in dominant else 500,
            max_image_coverage=1.0 if i in dominant else 0.0,
            image_dominant=i in dominant,
            reason="x",
        )
        for i in range(n)
    )
    return DocumentActivation(pages=pages)


def _region(
    *,
    co_located=False,
    high_value=False,
    strength=0.9,
    page=0,
    bbox=(10.0, 30.0, 30.0, 45.0),
    method="double_jpeg",
):
    methods = ("double_jpeg", "noise_inconsistency") if co_located else (method,)
    return TamperRegion(
        page_index=page,
        page_bbox=bbox,
        methods=methods,
        strength=strength,
        co_located=co_located,
        high_value=high_value,
        page_width_pt=64.0,
        page_height_pt=48.0,
    )


def _det(*, regions=(), method_errors=(), capability_gaps=(), global_signals=()):
    fires = tuple(
        MethodFire(r.page_index, "m", "v", r.page_bbox, r.strength, r.strength)
        for r in regions
    )
    return DetectionResult(
        regions=tuple(regions),
        fires=fires,
        global_signals=tuple(global_signals),
        method_errors=tuple(method_errors),
        capability_gaps=tuple(capability_gaps),
        analyzed_pages=(0,),
    )


# --------------------------------------------------------------------------- #
# INCONCLUSIVE
# --------------------------------------------------------------------------- #

class TestInconclusive:
    def test_digital_native_no_image_dominant_page(self):
        rep = score(_det(), _activation(dominant=()), CFG)
        assert rep.tier is ConfidenceTier.INCONCLUSIVE
        assert rep.score is None
        assert "digital-native" in rep.reasons[0]

    def test_capability_gaps_only_is_inconclusive(self):
        # Image-dominant page, but only deferred methods (no real analysis).
        det = _det(capability_gaps=(CapabilityGap(0, "ela"), CapabilityGap(0, "double_jpeg")))
        rep = score(det, _activation(), CFG)
        assert rep.tier is ConfidenceTier.INCONCLUSIVE
        assert rep.score is None
        assert any("deferred" in n for n in rep.notes)


# --------------------------------------------------------------------------- #
# LOW
# --------------------------------------------------------------------------- #

class TestLow:
    def test_global_only_is_low(self):
        det = _det(global_signals=(GlobalSignal(0, "ela", "v", 0.95, 0.95),))
        rep = score(det, _activation(), CFG)
        assert rep.tier is ConfidenceTier.LOW
        assert rep.score == CFG.score_low
        assert "rescan" in rep.reasons[0] or "innocent" in rep.reasons[0]

    def test_weak_lone_blob_is_low(self):
        det = _det(regions=(_region(strength=0.40),))  # below medium floor
        rep = score(det, _activation(), CFG)
        assert rep.tier is ConfidenceTier.LOW
        assert rep.findings[0].tier is ConfidenceTier.LOW

    def test_four_lone_regions_from_one_method_are_diffuse_low(self):
        regions = tuple(
            _region(
                bbox=(float(i * 4), 2.0, float(i * 4 + 2), 4.0),
                strength=1.0,
            )
            for i in range(CFG.diffuse_lone_min_count)
        )
        rep = score(_det(regions=regions), _activation(), CFG)
        assert rep.tier is ConfidenceTier.LOW
        assert all(f.tier is ConfidenceTier.LOW for f in rep.findings)
        assert "diffuse" in rep.reasons[0]

    def test_aggregate_lone_coverage_from_one_method_is_diffuse_low(self):
        regions = (
            _region(bbox=(0.0, 0.0, 32.0, 12.0), strength=1.0),
            _region(bbox=(32.0, 12.0, 64.0, 24.0), strength=1.0),
        )
        rep = score(_det(regions=regions), _activation(), CFG)
        assert rep.tier is ConfidenceTier.LOW
        assert all(f.tier is ConfidenceTier.LOW for f in rep.findings)

    def test_diffuse_method_does_not_demote_other_local_method(self):
        diffuse = tuple(
            _region(
                bbox=(float(i * 4), 2.0, float(i * 4 + 2), 4.0),
                strength=1.0,
            )
            for i in range(CFG.diffuse_lone_min_count)
        )
        local = _region(method="copy_move", bbox=(40.0, 30.0, 48.0, 38.0))
        rep = score(_det(regions=diffuse + (local,)), _activation(), CFG)
        assert rep.tier is ConfidenceTier.MEDIUM
        assert any(
            f.region.methods == ("copy_move",) and f.tier is ConfidenceTier.MEDIUM
            for f in rep.findings
        )


# --------------------------------------------------------------------------- #
# MEDIUM
# --------------------------------------------------------------------------- #

class TestMedium:
    def test_lone_strong_region_is_capped_medium(self):
        det = _det(regions=(_region(strength=0.90),))
        rep = score(det, _activation(), CFG)
        assert rep.tier is ConfidenceTier.MEDIUM
        assert rep.score == CFG.score_medium
        assert rep.findings[0].tier is ConfidenceTier.MEDIUM

    def test_method_error_only_is_medium(self):
        det = _det(method_errors=(MethodError(0, "noise_inconsistency", "RuntimeError"),))
        rep = score(det, _activation(), CFG)
        assert rep.tier is ConfidenceTier.MEDIUM
        assert rep.score == CFG.score_medium_method_error
        assert "errored" in rep.reasons[0]

    def test_page_spanning_colocated_region_is_capped_medium(self):
        band = _region(co_located=True, bbox=(0.0, 10.0, 64.0, 20.0))
        rep = score(_det(regions=(band,)), _activation(), CFG)
        assert rep.tier is ConfidenceTier.MEDIUM
        assert rep.score == CFG.score_medium
        assert rep.findings[0].tier is ConfidenceTier.MEDIUM
        assert "page-spanning" in rep.reasons[0]


# --------------------------------------------------------------------------- #
# HIGH
# --------------------------------------------------------------------------- #

class TestHigh:
    def test_colocated_region_is_high(self):
        det = _det(regions=(_region(co_located=True, high_value=False),))
        rep = score(det, _activation(), CFG)
        assert rep.tier is ConfidenceTier.HIGH
        assert rep.score == CFG.score_high
        assert rep.findings[0].tier is ConfidenceTier.HIGH
        assert "two independent" in rep.findings[0].reason

    def test_compact_colocated_region_remains_high(self):
        compact = _region(co_located=True, bbox=(10.0, 30.0, 30.0, 45.0))
        rep = score(_det(regions=(compact,)), _activation(), CFG)
        assert rep.tier is ConfidenceTier.HIGH
        assert rep.findings[0].tier is ConfidenceTier.HIGH

    def test_colocated_high_value_gets_bump(self):
        det = _det(regions=(_region(co_located=True, high_value=True),))
        rep = score(det, _activation(), CFG)
        assert rep.tier is ConfidenceTier.HIGH
        assert rep.score == CFG.score_high + CFG.score_high_value_bump
        assert rep.findings[0].region.high_value is True

    def test_high_worst_case_over_lone_region(self):
        # A co-located HIGH region AND a lone region on the same doc -> HIGH.
        det = _det(regions=(_region(co_located=True), _region(co_located=False, strength=0.9)))
        rep = score(det, _activation(), CFG)
        assert rep.tier is ConfidenceTier.HIGH
        tiers = {f.tier for f in rep.findings}
        assert ConfidenceTier.HIGH in tiers and ConfidenceTier.MEDIUM in tiers

    def test_single_method_never_high_alone(self):
        # Even a very strong lone single-method region stays MEDIUM.
        det = _det(regions=(_region(co_located=False, strength=1.0),))
        rep = score(det, _activation(), CFG)
        assert rep.tier is not ConfidenceTier.HIGH
