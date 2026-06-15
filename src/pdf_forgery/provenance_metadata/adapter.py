"""Adapter between the rich :class:`ProvenanceReport` and the core stage schema.

Mirrors the other stages' adapters: maps the report onto a
:class:`~pdf_forgery.core.types.StageResult` (report carried as ``payload``) and
provides JSON / human-summary renderers.
"""

from __future__ import annotations

import json

from ..core.types import ConfidenceTier, Evidence, Finding, StageResult
from .models import ProvenanceFinding, ProvenanceReport

STAGE_NAME = "provenance_metadata"


def _finding_to_core(f: ProvenanceFinding) -> Finding:
    evidence = tuple(Evidence(label=k, before="", after=v) for k, v in f.detail)
    return Finding(
        stage=STAGE_NAME,
        tier=f.tier,
        reason=f.reason,
        page=None,            # provenance is document-level, not page-bound
        object_ids=(),
        before=None,
        after=None,
        high_value=None,
        evidence=evidence,
    )


def report_to_stage_result(report: ProvenanceReport) -> StageResult:
    """Convert a :class:`ProvenanceReport` into a core :class:`StageResult`."""
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

    findings = tuple(_finding_to_core(f) for f in report.findings)
    n = len(findings)
    if report.tier is ConfidenceTier.INCONCLUSIVE:
        summary = f"{STAGE_NAME}: inconclusive (no readable metadata)"
    else:
        noun = "signal" if n == 1 else "signals"
        score_txt = "n/a" if report.score is None else str(report.score)
        suffix = " (corroborating only — never conclusive)" if n else ""
        summary = (
            f"{STAGE_NAME}: {report.tier.value.upper()} (score {score_txt}); "
            f"{n} {noun}{suffix}"
        )

    return StageResult(
        stage=STAGE_NAME,
        tier=report.tier,
        score=report.score,
        findings=findings,
        summary=summary,
        reasons=report.reasons,
        notes=report.notes,
        ok=True,
        error=None,
        payload=report,
    )


def stage_result_to_report(result: StageResult) -> ProvenanceReport:
    """Recover the rich :class:`ProvenanceReport` carried by a stage result."""
    payload = result.payload
    if not isinstance(payload, ProvenanceReport):
        raise TypeError(
            "StageResult.payload is not a ProvenanceReport; "
            "cannot render via the provenance_metadata adapter"
        )
    return payload


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def report_to_dict(report: ProvenanceReport) -> dict:
    """Serialise a :class:`ProvenanceReport` to a JSON-ready dict."""
    return {
        "stage": STAGE_NAME,
        "path": report.path,
        "ok": report.ok,
        "error": report.error,
        "raw_size": report.raw_size,
        "tier": report.tier.value,
        "score": report.score,
        "producer": report.producer,
        "creator": report.creator,
        "creation_date": report.creation_date,
        "mod_date": report.mod_date,
        "xmp_producer": report.xmp_producer,
        "id_pair": list(report.id_pair) if report.id_pair else None,
        "reasons": list(report.reasons),
        "notes": list(report.notes),
        "findings": [
            {
                "kind": f.kind.value,
                "tier": f.tier.value,
                "reason": f.reason,
                "detail": {k: v for k, v in f.detail},
            }
            for f in report.findings
        ],
    }


def render_json(report: ProvenanceReport, *, indent: int = 2) -> str:
    """Render a single report as a JSON object."""
    return json.dumps(report_to_dict(report), indent=indent, ensure_ascii=False)


def render_summary(report: ProvenanceReport) -> str:
    """Render a human-readable summary; confidence is ADVISORY + corroborating."""
    lines: list[str] = []
    lines.append(f"=== provenance_metadata: {report.path} ===")
    if not report.ok:
        lines.append(f"  ERROR: {report.error}")
        return "\n".join(lines)

    score_txt = "n/a" if report.score is None else str(report.score)
    lines.append(
        f"  Confidence: {report.tier.value.upper()} (score {score_txt}) — "
        "ADVISORY, corroborating only (never conclusive on its own)"
    )
    if report.producer is not None:
        lines.append(f"  Producer: {report.producer!r}")
    if report.creator is not None:
        lines.append(f"  Creator : {report.creator!r}")
    if report.creation_date is not None or report.mod_date is not None:
        lines.append(
            f"  Dates   : created {report.creation_date!r}, modified {report.mod_date!r}"
        )
    if not report.findings:
        lines.append("  No provenance anomalies.")
    for i, f in enumerate(report.findings, 1):
        lines.append(f"  [{i}] {f.tier.value.upper()} {f.kind.value}")
        lines.append(f"        {f.reason}")
    lines.append("  (Provenance is corroborating evidence; a human reviewer decides.)")
    return "\n".join(lines)


def render_stage_json(result: StageResult, *, indent: int = 2) -> str:
    return render_json(stage_result_to_report(result), indent=indent)


def render_stage_summary(result: StageResult) -> str:
    return render_summary(stage_result_to_report(result))
