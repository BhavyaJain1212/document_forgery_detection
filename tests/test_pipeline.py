"""Tests for the top-level orchestrator (pdf_forgery.pipeline).

For now the pipeline just runs a list of stages and returns their StageResults
(fusion comes later). These tests prove it threads a shared context, collects
results in order, and degrades gracefully on bad input / misbehaving stages.
"""

from __future__ import annotations

from pdf_forgery.core import AnalysisContext, ConfidenceTier, StageResult
from pdf_forgery.pipeline import run_pipeline, run_pipeline_on_path
from pdf_forgery.revision_recovery import RevisionRecoveryStage, analyze_path_as_stage


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

def test_run_pipeline_returns_one_result_per_stage(forged_pdf):
    raw = forged_pdf.read_bytes()
    stages = [RevisionRecoveryStage()]
    results = run_pipeline(raw, stages, path=str(forged_pdf))

    assert len(results) == 1
    r = results[0]
    assert r.stage == "revision_recovery"
    assert r.tier is ConfidenceTier.HIGH
    # Same verdict as running the stage entry point directly.
    direct = analyze_path_as_stage(forged_pdf)
    assert r.tier is direct.tier
    assert r.score == direct.score


def test_run_pipeline_on_path_matches_bytes(clean_pdf):
    results = run_pipeline_on_path(clean_pdf, [RevisionRecoveryStage()])
    assert len(results) == 1
    assert results[0].tier is ConfidenceTier.INCONCLUSIVE


def test_results_preserve_stage_order():
    class StubStage:
        def __init__(self, name):
            self.name = name

        def run(self, pdf_bytes, ctx):
            return StageResult(stage=self.name, tier=ConfidenceTier.LOW, score=0)

    stages = [StubStage("a"), StubStage("b"), StubStage("c")]
    results = run_pipeline(b"%PDF-1.7\n", stages)
    assert [r.stage for r in results] == ["a", "b", "c"]


def test_pipeline_passes_one_shared_context():
    seen: list[int] = []

    class ProbeStage:
        name = "probe"

        def run(self, pdf_bytes, ctx):
            assert isinstance(ctx, AnalysisContext)
            seen.append(id(ctx))
            return StageResult(stage=self.name, tier=ConfidenceTier.LOW, score=0)

    run_pipeline(b"%PDF-1.7\n", [ProbeStage(), ProbeStage()])
    # Both stages received the very same context object.
    assert len(seen) == 2 and seen[0] == seen[1]


# --------------------------------------------------------------------------- #
# Graceful degradation
# --------------------------------------------------------------------------- #

def test_missing_path_yields_failed_result_per_stage(tmp_path):
    missing = tmp_path / "nope.pdf"
    results = run_pipeline_on_path(missing, [RevisionRecoveryStage()])
    assert len(results) == 1
    assert results[0].ok is False
    assert "not found" in results[0].error


def test_misbehaving_stage_is_captured_not_raised():
    class BoomStage:
        name = "boom"

        def run(self, pdf_bytes, ctx):
            raise RuntimeError("kaboom")

    results = run_pipeline(b"%PDF-1.7\n", [BoomStage()])
    assert results[0].ok is False
    assert "kaboom" in results[0].error
    assert results[0].tier is ConfidenceTier.INCONCLUSIVE
