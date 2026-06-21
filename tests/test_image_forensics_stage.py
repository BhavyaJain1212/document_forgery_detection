"""Stage 6 — Session 6.3 tests: the ImageForensicsStage + fusion integration.

* The stage conforms to ``core.Stage`` and never raises.
* End-to-end (activation → detect → score → adapt) over real fixtures with an
  injected provider: digital-native → INCONCLUSIVE; scanned + co-located region
  → HIGH with a Finding bbox over the altered region; the real ClassicalProvider
  (deferred DSP) → INCONCLUSIVE, safe live.
* Fusion: Stage 6 is SUBSTANTIVE — HIGH stands alone; INCONCLUSIVE never drags a
  parse-side HIGH down; Stage 6 MEDIUM + provenance corroboration → HIGH;
  registering Stage 6 does not change the headline on a digital-native doc.
"""

from __future__ import annotations

import numpy as np
import pytest

import make_fixtures
import make_image_forensics_fixtures as fixtures

from pdf_forgery.core.context import AnalysisContext
from pdf_forgery.core.stage import Stage
from pdf_forgery.core.types import ConfidenceTier, StageResult
from pdf_forgery.fusion import fuse
from pdf_forgery.image_forensics import (
    ClassicalProvider,
    ImageForensicsConfig,
    ImageForensicsStage,
)
from pdf_forgery.image_forensics.engine import ForensicMap, ForensicProvenance
from pdf_forgery.image_forensics import stage as stage_mod

CFG = ImageForensicsConfig()


# --------------------------------------------------------------------------- #
# controllable provider (canned heatmaps)
# --------------------------------------------------------------------------- #

class _CannedMethod:
    def __init__(self, name, heatmap, *, requires_jpeg=False):
        self.name = name
        self.version = "canned-1"
        self._hm = heatmap
        self._requires_jpeg = requires_jpeg

    def applicable(self, image, cfg):
        return (not self._requires_jpeg) or image.is_jpeg

    def analyze(self, image, cfg):
        return ForensicMap(method=self.name, version=self.version, heatmap=self._hm)


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


def _lower_band_heatmap():
    hm = np.zeros((100, 100), dtype=np.float64)
    hm[60:90, 40:70] = 0.9  # lower-middle = the amount band
    return hm


def _colocated_provider():
    hm = _lower_band_heatmap()
    return _CannedProvider([
        _CannedMethod("double_jpeg", hm, requires_jpeg=True),
        _CannedMethod("noise_inconsistency", hm),
    ])


# --------------------------------------------------------------------------- #
# Stage contract + end-to-end
# --------------------------------------------------------------------------- #

class TestStage:
    def test_conforms_to_protocol(self):
        assert isinstance(ImageForensicsStage(), Stage)
        assert ImageForensicsStage().name == "image_forensics"

    def test_digital_native_is_inconclusive(self):
        ctx = AnalysisContext(make_fixtures.build_clean())
        res = ImageForensicsStage(provider=_colocated_provider()).run(ctx.pdf_bytes, ctx)
        assert res.ok is True
        assert res.tier is ConfidenceTier.INCONCLUSIVE
        assert res.score is None
        assert res.findings == ()

    def test_spliced_amount_is_high_with_bbox_over_region(self):
        pdf, _ = fixtures.build_jpeg_image_pdf()
        ctx = AnalysisContext(pdf)
        res = ImageForensicsStage(provider=_colocated_provider()).run(pdf, ctx)
        assert res.ok is True
        assert res.tier is ConfidenceTier.HIGH
        assert res.score >= 70
        assert len(res.findings) == 1
        finding = res.findings[0]
        assert finding.tier is ConfidenceTier.HIGH
        assert finding.high_value == "amount"
        assert finding.page == 0
        # The bbox lives on the rich payload region (FindingLocation shape) and
        # sits in the lower (amount) band — over the altered area.
        region = res.payload.findings[0].region
        assert region.page_bbox is not None
        _x0, top, _x1, bottom = region.page_bbox
        assert top > region.page_height_pt * CFG.high_value_band_top_frac
        assert bottom <= region.page_height_pt + 1e-6

    def test_real_classical_provider_is_inconclusive_live_safe(self):
        # Deferred DSP → capability gaps → INCONCLUSIVE (never a false MEDIUM).
        if not ClassicalProvider().is_available():
            pytest.skip("classical CPU stack unavailable")
        pdf, _ = fixtures.build_jpeg_image_pdf()
        ctx = AnalysisContext(pdf)
        res = ImageForensicsStage().run(pdf, ctx)  # default = ClassicalProvider
        assert res.ok is True
        assert res.tier is ConfidenceTier.INCONCLUSIVE
        assert any("deferred" in n for n in res.notes)

    def test_internal_failure_is_ok_false_never_raises(self, monkeypatch):
        monkeypatch.setattr(
            stage_mod, "activate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        pdf, _ = fixtures.build_jpeg_image_pdf()
        ctx = AnalysisContext(pdf)
        res = ImageForensicsStage().run(pdf, ctx)
        assert res.ok is False
        assert res.tier is ConfidenceTier.INCONCLUSIVE
        assert "RuntimeError" in (res.error or "")


# --------------------------------------------------------------------------- #
# Fusion integration — Stage 6 is SUBSTANTIVE
# --------------------------------------------------------------------------- #

IMG = "image_forensics"


def _r(stage, tier, score=None):
    return StageResult(stage=stage, tier=tier, score=score, findings=(), summary="",
                       reasons=(), notes=(), ok=True)


class TestFusion:
    def test_stage6_high_stands_alone_on_scanned_doc(self):
        # Parse-side stages all INCONCLUSIVE (scanned) — Stage 6 originates HIGH.
        res = [
            _r("revision_recovery", ConfidenceTier.INCONCLUSIVE),
            _r("font_forensics", ConfidenceTier.INCONCLUSIVE),
            _r("provenance_metadata", ConfidenceTier.INCONCLUSIVE),
            _r(IMG, ConfidenceTier.HIGH, 85),
        ]
        a = fuse(res)
        assert a.tier is ConfidenceTier.HIGH
        assert a.score == 85
        assert IMG in a.contributing_stages

    def test_stage6_inconclusive_never_drags_down_parse_side_high(self):
        res = [
            _r("revision_recovery", ConfidenceTier.HIGH, 95),
            _r(IMG, ConfidenceTier.INCONCLUSIVE),
        ]
        a = fuse(res)
        assert a.tier is ConfidenceTier.HIGH
        assert a.score == 95

    def test_stage6_medium_plus_provenance_escalates_to_high(self):
        res = [
            _r("revision_recovery", ConfidenceTier.INCONCLUSIVE),
            _r(IMG, ConfidenceTier.MEDIUM, 50),
            _r("provenance_metadata", ConfidenceTier.MEDIUM, 50),  # corroborator
        ]
        a = fuse(res)
        assert a.tier is ConfidenceTier.HIGH
        assert a.score >= 70
        assert IMG in a.contributing_stages

    def test_adding_inconclusive_stage6_does_not_change_headline(self):
        # Regression: a digital-native doc's headline is identical with/without
        # Stage 6 (which is INCONCLUSIVE there).
        base = [
            _r("revision_recovery", ConfidenceTier.INCONCLUSIVE),
            _r("font_forensics", ConfidenceTier.LOW, 15),
            _r("invoice_arithmetic", ConfidenceTier.MEDIUM, 65),
            _r("provenance_metadata", ConfidenceTier.LOW, 0),
        ]
        before = fuse(base)
        after = fuse(base + [_r(IMG, ConfidenceTier.INCONCLUSIVE)])
        assert (after.tier, after.score) == (before.tier, before.score)
