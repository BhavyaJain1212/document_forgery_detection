"""Adapter between revision recovery's rich models and the core stage schema.

Revision recovery keeps its detailed :class:`AnalysisReport` (revisions, text
diffs, object diffs, findings with token-level detail). The pipeline, however,
only speaks the small stage-agnostic vocabulary in :mod:`pdf_forgery.core.types`.

This module bridges the two WITHOUT changing detection logic or scoring outcomes:

    report_to_stage_result(report) -> StageResult   (maps onto the core schema)
    stage_result_to_report(result) -> AnalysisReport (recovers the rich payload)

The ``StageResult`` carries the original ``AnalysisReport`` as its ``payload`` so
the existing JSON / human-summary renderers in ``report.py`` keep working — see
``render_stage_json`` / ``render_stage_summary`` below, which simply unwrap the
payload and delegate to the unchanged renderers.
"""

from __future__ import annotations

from ..core.types import ConfidenceTier, Evidence, Finding, StageResult
from .models import (
    AnalysisReport,
    Finding as RRFinding,
    ObjectChangeClass,
)
from .report import render_json, render_summary

STAGE_NAME = "revision_recovery"


# ---------------------------------------------------------------------------
# Per-finding tier (advisory annotation; does NOT alter the scoring outcome)
# ---------------------------------------------------------------------------

# Object classes the rubric treats as "needs review" on their own.
_MEDIUM_CLASSES = frozenset({ObjectChangeClass.OVERLAY, ObjectChangeClass.FIELD_EDIT})


def _finding_tier(f: RRFinding) -> ConfidenceTier:
    """Advisory tier for ONE finding, consistent with the scoring rubric.

    This annotates each finding; it never feeds back into the overall score (that
    still comes verbatim from ``scoring.score``). A substantive text edit in a
    CONTENT object is HIGH; a covering overlay / field edit / content-without-text
    change is MEDIUM; anything else benign is LOW.
    """
    classes = set(f.object_classes)
    has_content = ObjectChangeClass.CONTENT in classes
    if f.token_changes and has_content:
        return ConfidenceTier.HIGH
    if f.token_changes and f.is_high_value:
        return ConfidenceTier.HIGH
    if classes & _MEDIUM_CLASSES:
        return ConfidenceTier.MEDIUM
    if has_content and not f.token_changes:
        # CONTENT stream changed but the text layer is unchanged — possible
        # overlay / inpainting; the rubric routes this to MEDIUM (OCR cross-check).
        return ConfidenceTier.MEDIUM
    return ConfidenceTier.LOW


def _rr_finding_to_core(f: RRFinding) -> Finding:
    """Map one revision-recovery :class:`Finding` onto the core schema."""
    before = f.before_text or None
    after = f.after_text or None
    evidence = tuple(
        Evidence(
            label=(tc.high_value.value if tc.high_value else "token"),
            before=tc.before,
            after=tc.after,
        )
        for tc in f.token_changes
    )
    return Finding(
        stage=STAGE_NAME,
        tier=_finding_tier(f),
        reason=f.summary,
        page=f.page_index,
        object_ids=f.object_ids,
        before=before,
        after=after,
        high_value=(f.high_value_kind.value if f.high_value_kind else None),
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Report <-> StageResult
# ---------------------------------------------------------------------------

def report_to_stage_result(report: AnalysisReport) -> StageResult:
    """Convert an :class:`AnalysisReport` into a core :class:`StageResult`.

    The full report is preserved as ``payload`` so the existing renderers keep
    working. Tiers, scores, and findings are carried over unchanged in meaning.
    """
    scoring = report.scoring

    if not report.ok:
        return StageResult(
            stage=STAGE_NAME,
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            findings=(),
            summary=f"{STAGE_NAME}: could not analyse file ({report.error})",
            reasons=(),
            notes=report.notes,
            ok=False,
            error=report.error,
            payload=report,
        )

    tier = scoring.tier if scoring else ConfidenceTier.INCONCLUSIVE
    score = scoring.score if scoring else None
    reasons = scoring.reasons if scoring else ()

    findings = tuple(_rr_finding_to_core(f) for f in report.findings)

    n = len(findings)
    if tier is ConfidenceTier.INCONCLUSIVE:
        summary = f"{STAGE_NAME}: inconclusive (single revision; route to later stages)"
    else:
        noun = "finding" if n == 1 else "findings"
        score_txt = "n/a" if score is None else str(score)
        summary = (
            f"{STAGE_NAME}: {tier.value.upper()} (score {score_txt}); "
            f"{n} {noun}"
        )

    return StageResult(
        stage=STAGE_NAME,
        tier=tier,
        score=score,
        findings=findings,
        summary=summary,
        reasons=reasons,
        notes=report.notes,
        ok=True,
        error=None,
        payload=report,
    )


def stage_result_to_report(result: StageResult) -> AnalysisReport:
    """Recover the rich :class:`AnalysisReport` carried by a stage result.

    Raises ``TypeError`` if the result was not produced by this stage (its
    ``payload`` is not an :class:`AnalysisReport`).
    """
    payload = result.payload
    if not isinstance(payload, AnalysisReport):
        raise TypeError(
            "StageResult.payload is not an AnalysisReport; "
            "cannot render via the revision_recovery adapter"
        )
    return payload


# ---------------------------------------------------------------------------
# Rendering passthrough — keeps the existing JSON / summary output working
# ---------------------------------------------------------------------------

def render_stage_json(result: StageResult, *, indent: int = 2) -> str:
    """Render a revision-recovery :class:`StageResult` as the original JSON."""
    return render_json(stage_result_to_report(result), indent=indent)


def render_stage_summary(result: StageResult) -> str:
    """Render a revision-recovery :class:`StageResult` as the original summary."""
    return render_summary(stage_result_to_report(result))
