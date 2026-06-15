"""End-to-end tests for the font-forensics stage over the shipped fixtures.

Asserts the known-positive (amount re-embedded in a foreign subset) -> HIGH and
the known-negative (genuine bold-header multi-font invoice) -> LOW, that the
stage plugs into the pipeline alongside revision recovery, and that the adapter /
renderers behave.
"""

from __future__ import annotations

import json

from pdf_forgery.core import AnalysisContext, ConfidenceTier, StageResult, Stage
from pdf_forgery.font_forensics import (
    FontForensicsStage,
    analyze_bytes,
    analyze_path,
    analyze_path_as_stage,
    render_stage_json,
    render_stage_summary,
    report_to_stage_result,
    stage_result_to_report,
)
from pdf_forgery.pipeline import run_pipeline
from pdf_forgery.revision_recovery import RevisionRecoveryStage


# --------------------------------------------------------------------------- #
# Known-positive: HIGH on the re-embedded amount
# --------------------------------------------------------------------------- #

def test_forged_fixture_high(font_forged_pdf):
    report = analyze_path(font_forged_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.HIGH
    assert report.score == 95
    assert len(report.findings) == 1

    f = report.findings[0]
    assert f.token == "50,000"
    assert f.high_value.value == "amount"
    assert f.token_font == "GHIJKL+Helvetica"
    assert f.context_font == "ABCDEF+Helvetica"
    # bbox is the amount token's box.
    assert f.bbox[2] > f.bbox[0] and f.bbox[3] > f.bbox[1]
    assert any("high-value" in r for r in report.reasons)


def test_forged_revision_recovery_is_inconclusive_font_catches_it(font_forged_pdf):
    """The whole point: single-revision file -> rev-recovery blind, fonts catch it."""
    raw = font_forged_pdf.read_bytes()
    results = run_pipeline(
        raw, [RevisionRecoveryStage(), FontForensicsStage()], path=str(font_forged_pdf)
    )
    by_stage = {r.stage: r for r in results}
    assert by_stage["revision_recovery"].tier is ConfidenceTier.INCONCLUSIVE
    assert by_stage["font_forensics"].tier is ConfidenceTier.HIGH
    assert by_stage["font_forensics"].score == 95


# --------------------------------------------------------------------------- #
# Known-negative: LOW on a genuine multi-font invoice (false-positive guard)
# --------------------------------------------------------------------------- #

def test_multifont_fixture_low_no_findings(font_multifont_pdf):
    report = analyze_path(font_multifont_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.LOW
    assert report.findings == ()
    # It really is multi-font (bold headers over body), just benign.
    assert "Helvetica" in report.distinct_fonts
    assert "Helvetica-Bold" in report.distinct_fonts


# --------------------------------------------------------------------------- #
# Stage protocol / context plumbing
# --------------------------------------------------------------------------- #

def test_stage_conforms_to_protocol():
    assert isinstance(FontForensicsStage(), Stage)
    assert FontForensicsStage().name == "font_forensics"


def test_stage_uses_shared_context(font_forged_pdf):
    raw = font_forged_pdf.read_bytes()
    stage = FontForensicsStage()
    with AnalysisContext(raw, path=str(font_forged_pdf)) as ctx:
        result = stage.run(raw, ctx)
    direct = analyze_path_as_stage(font_forged_pdf)
    assert result.tier is direct.tier
    assert result.score == direct.score
    assert [f.reason for f in result.findings] == [f.reason for f in direct.findings]


# --------------------------------------------------------------------------- #
# Adapter + rendering
# --------------------------------------------------------------------------- #

def test_adapter_round_trip_and_core_finding(font_forged_pdf):
    report = analyze_path(font_forged_pdf)
    result = report_to_stage_result(report)

    assert isinstance(result, StageResult)
    assert result.stage == "font_forensics"
    assert result.tier is ConfidenceTier.HIGH
    assert stage_result_to_report(result) is report

    (cf,) = result.findings
    assert cf.tier is ConfidenceTier.HIGH
    assert cf.page == 0
    assert cf.high_value == "amount"
    # Evidence carries the token, the conflicting fonts, and the bbox.
    labels = {e.label for e in cf.evidence}
    assert {"token", "conflicting_fonts", "bbox"} <= labels
    token_ev = next(e for e in cf.evidence if e.label == "token")
    assert token_ev.after == "50,000"
    fonts_ev = next(e for e in cf.evidence if e.label == "conflicting_fonts")
    assert fonts_ev.before == "ABCDEF+Helvetica"
    assert fonts_ev.after == "GHIJKL+Helvetica"


def test_render_json_and_summary(font_forged_pdf):
    result = analyze_path_as_stage(font_forged_pdf)

    doc = json.loads(render_stage_json(result))
    assert doc["stage"] == "font_forensics"
    assert doc["tier"] == "high"
    assert doc["findings"][0]["token"] == "50,000"
    assert doc["findings"][0]["bbox"]

    summary = render_stage_summary(result)
    assert "ADVISORY" in summary
    assert "50,000" in summary
    assert "GHIJKL+Helvetica" in summary


# --------------------------------------------------------------------------- #
# Graceful degradation (never crash)
# --------------------------------------------------------------------------- #

def test_garbage_bytes_inconclusive_not_crash():
    report = analyze_bytes(b"%PDF-1.4 not really a pdf", "garbage.pdf")
    assert report.ok is True
    assert report.tier is ConfidenceTier.INCONCLUSIVE
    assert report.findings == ()


def test_missing_path_is_not_ok():
    report = analyze_path("/no/such/file.pdf")
    assert report.ok is False
    assert report.error is not None
    assert report.tier is ConfidenceTier.INCONCLUSIVE
