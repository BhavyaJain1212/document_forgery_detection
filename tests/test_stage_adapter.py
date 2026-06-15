"""The refactored stage produces IDENTICAL tiers/findings on the fixtures.

These are the acceptance tests for the refactor: running revision recovery
through the new core stage schema must yield the same verdict, the same
before/after evidence, and the same rendered JSON / summary as the original
``analyze_path`` + ``report.py`` path — proving the refactor changed plumbing,
not detection.
"""

from __future__ import annotations

import json

from pdf_forgery.core import ConfidenceTier, StageResult
from pdf_forgery.revision_recovery import (
    RevisionRecoveryStage,
    analyze_path,
    analyze_path_as_stage,
    render_json,
    render_stage_json,
    render_stage_summary,
    render_summary,
    report_to_stage_result,
    stage_result_to_report,
)
from pdf_forgery.core import AnalysisContext


# --------------------------------------------------------------------------- #
# Forged fixture: HIGH, with the exact amount edit, mapped onto the core schema
# --------------------------------------------------------------------------- #

def test_forged_stage_result_matches_report(forged_pdf):
    report = analyze_path(forged_pdf)
    result = report_to_stage_result(report)

    assert isinstance(result, StageResult)
    assert result.stage == "revision_recovery"
    assert result.ok is True
    # Overall tier/score carried over verbatim from scoring.
    assert result.tier is ConfidenceTier.HIGH
    assert result.tier is report.scoring.tier
    assert result.score == report.scoring.score == 95
    assert result.reasons == report.scoring.reasons
    # The rich report is preserved as the payload.
    assert stage_result_to_report(result) is report


def test_forged_finding_before_after_preserved(forged_pdf):
    result = analyze_path_as_stage(forged_pdf)

    text_findings = [f for f in result.findings if f.before or f.after]
    assert len(text_findings) == 1
    f = text_findings[0]

    assert f.stage == "revision_recovery"
    assert f.tier is ConfidenceTier.HIGH
    assert f.page == 0
    assert f.object_ids  # at least one "<obj> <gen>"
    assert f.before == "5,000"
    assert f.after == "50,000"
    assert f.high_value == "amount"
    # Granular evidence mirrors the headline change.
    assert f.evidence
    assert f.evidence[0].label == "amount"
    assert (f.evidence[0].before, f.evidence[0].after) == ("5,000", "50,000")


# --------------------------------------------------------------------------- #
# Clean fixture: INCONCLUSIVE, no findings
# --------------------------------------------------------------------------- #

def test_clean_stage_result_inconclusive(clean_pdf):
    result = analyze_path_as_stage(clean_pdf)
    assert result.ok is True
    assert result.tier is ConfidenceTier.INCONCLUSIVE
    assert result.score is None
    assert result.findings == ()
    assert "inconclusive" in result.summary.lower()


# --------------------------------------------------------------------------- #
# The RevisionRecoveryStage class yields the same thing
# --------------------------------------------------------------------------- #

def test_stage_class_equivalent_to_analyze(forged_pdf):
    raw = forged_pdf.read_bytes()
    stage = RevisionRecoveryStage()
    with AnalysisContext(raw, path=str(forged_pdf)) as ctx:
        result = stage.run(raw, ctx)

    direct = analyze_path_as_stage(forged_pdf)
    assert result.tier is direct.tier
    assert result.score == direct.score
    assert [(f.before, f.after, f.tier) for f in result.findings] == [
        (f.before, f.after, f.tier) for f in direct.findings
    ]


# --------------------------------------------------------------------------- #
# Rendering passthrough: identical JSON / summary as the original path
# --------------------------------------------------------------------------- #

def test_render_passthrough_identical(forged_pdf, clean_pdf):
    for pdf in (forged_pdf, clean_pdf):
        report = analyze_path(pdf)
        result = report_to_stage_result(report)

        assert render_stage_json(result) == render_json(report)
        assert render_stage_summary(result) == render_summary(report)
        # JSON still parses and carries the same tier.
        assert json.loads(render_stage_json(result))["scoring"]["tier"] == (
            report.scoring.tier.value
        )
