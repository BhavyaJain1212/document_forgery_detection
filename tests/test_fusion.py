"""Fusion of per-stage results into one overall assessment."""

from __future__ import annotations

from pdf_forgery.core.types import ConfidenceTier, StageResult
from pdf_forgery.fusion import FusionConfig, fuse, render_overall_summary


def _result(stage: str, tier: ConfidenceTier, score=None, ok=True, error=None) -> StageResult:
    return StageResult(
        stage=stage, tier=tier, score=score, findings=(), summary="",
        reasons=(), notes=(), ok=ok, error=error,
    )


REV = "revision_recovery"
FONT = "font_forensics"
ARITH = "invoice_arithmetic"
PROV = "provenance_metadata"  # the corroborator


# --------------------------------------------------------------------------- #
# Substantive HIGH dominates.
# --------------------------------------------------------------------------- #

def test_substantive_high_is_high():
    res = [
        _result(REV, ConfidenceTier.HIGH, 95),
        _result(FONT, ConfidenceTier.LOW, 15),
        _result(PROV, ConfidenceTier.LOW, 0),
    ]
    a = fuse(res)
    assert a.tier is ConfidenceTier.HIGH
    assert a.score == 95


# --------------------------------------------------------------------------- #
# Lone substantive MEDIUM stays MEDIUM (no corroboration).
# --------------------------------------------------------------------------- #

def test_lone_medium_stays_medium():
    res = [
        _result(REV, ConfidenceTier.INCONCLUSIVE),
        _result(FONT, ConfidenceTier.LOW, 15),
        _result(ARITH, ConfidenceTier.MEDIUM, 65),
        _result(PROV, ConfidenceTier.LOW, 0),
    ]
    a = fuse(res)
    assert a.tier is ConfidenceTier.MEDIUM
    assert a.score == 65


# --------------------------------------------------------------------------- #
# Substantive MEDIUM + corroborator firing -> escalate to HIGH (the Sejda case).
# --------------------------------------------------------------------------- #

def test_medium_plus_corroborator_escalates_to_high():
    res = [
        _result(REV, ConfidenceTier.INCONCLUSIVE),
        _result(FONT, ConfidenceTier.LOW, 15),
        _result(ARITH, ConfidenceTier.MEDIUM, 65),
        _result(PROV, ConfidenceTier.MEDIUM, 55),
    ]
    a = fuse(res)
    assert a.tier is ConfidenceTier.HIGH
    assert a.score >= 70
    assert ARITH in a.contributing_stages and PROV in a.contributing_stages


# --------------------------------------------------------------------------- #
# Two independent substantive MEDIUMs -> HIGH (no corroborator needed).
# --------------------------------------------------------------------------- #

def test_two_substantive_mediums_escalate():
    res = [
        _result(FONT, ConfidenceTier.MEDIUM, 50),
        _result(ARITH, ConfidenceTier.MEDIUM, 65),
        _result(PROV, ConfidenceTier.LOW, 0),
    ]
    a = fuse(res)
    assert a.tier is ConfidenceTier.HIGH


# --------------------------------------------------------------------------- #
# Corroboration cannot lift an all-clean substantive picture.
# --------------------------------------------------------------------------- #

def test_corroborator_cannot_lift_low():
    # Honest bill merely re-rendered through a web editor: arithmetic/font clean.
    res = [
        _result(REV, ConfidenceTier.INCONCLUSIVE),
        _result(FONT, ConfidenceTier.LOW, 15),
        _result(ARITH, ConfidenceTier.LOW, 10),
        _result(PROV, ConfidenceTier.MEDIUM, 55),
    ]
    a = fuse(res)
    assert a.tier is ConfidenceTier.LOW


def test_corroborator_alone_is_low():
    # All substantive stages inconclusive; only provenance fired.
    res = [
        _result(REV, ConfidenceTier.INCONCLUSIVE),
        _result(ARITH, ConfidenceTier.INCONCLUSIVE),
        _result(PROV, ConfidenceTier.MEDIUM, 55),
    ]
    a = fuse(res)
    assert a.tier is ConfidenceTier.LOW


# --------------------------------------------------------------------------- #
# All inconclusive -> inconclusive.
# --------------------------------------------------------------------------- #

def test_all_inconclusive():
    res = [
        _result(REV, ConfidenceTier.INCONCLUSIVE),
        _result(ARITH, ConfidenceTier.INCONCLUSIVE),
        _result(PROV, ConfidenceTier.INCONCLUSIVE),
    ]
    a = fuse(res)
    assert a.tier is ConfidenceTier.INCONCLUSIVE
    assert a.score is None


def test_no_results_inconclusive():
    a = fuse([])
    assert a.tier is ConfidenceTier.INCONCLUSIVE


# --------------------------------------------------------------------------- #
# Failed stages are noted, not counted as signal.
# --------------------------------------------------------------------------- #

def test_failed_stage_noted_not_counted():
    res = [
        _result(ARITH, ConfidenceTier.MEDIUM, 65),
        _result(FONT, ConfidenceTier.INCONCLUSIVE, ok=False, error="boom"),
        _result(PROV, ConfidenceTier.LOW, 0),
    ]
    a = fuse(res)
    assert a.tier is ConfidenceTier.MEDIUM  # lone medium, no corroboration
    assert any("font_forensics" in n and "boom" in n for n in a.notes)


# --------------------------------------------------------------------------- #
# Custom roles: treat a stage as a corroborator via config.
# --------------------------------------------------------------------------- #

def test_config_corroborator_role():
    cfg = FusionConfig(corroborator_stages=frozenset({FONT, PROV}))
    res = [
        _result(ARITH, ConfidenceTier.MEDIUM, 65),
        _result(FONT, ConfidenceTier.MEDIUM, 50),   # now a corroborator
    ]
    a = fuse(res, cfg)
    # arithmetic MEDIUM + font (corroborator) firing -> HIGH
    assert a.tier is ConfidenceTier.HIGH


def test_render_summary_contains_overall():
    res = [_result(ARITH, ConfidenceTier.MEDIUM, 65), _result(PROV, ConfidenceTier.MEDIUM, 55)]
    text = render_overall_summary(fuse(res))
    assert "OVERALL ASSESSMENT: HIGH" in text
    assert "ADVISORY" in text
