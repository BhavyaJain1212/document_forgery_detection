"""Adapter between the rich :class:`FontReport` and the core stage schema.

Maps a font-forensics report onto the shared
:class:`~pdf_forgery.core.types.StageResult` (carrying the report as ``payload``)
and provides JSON / human-summary renderers. Mirrors
``revision_recovery.adapter`` so the orchestrator treats both stages uniformly.
"""

from __future__ import annotations

import json

from ..core.types import ConfidenceTier, Evidence, Finding, StageResult
from .models import FontFinding, FontReport

STAGE_NAME = "font_forensics"


# ---------------------------------------------------------------------------
# FontFinding -> core Finding
# ---------------------------------------------------------------------------

def _bbox_str(bbox: tuple[float, float, float, float]) -> str:
    return ", ".join(f"{v:.1f}" for v in bbox)


def _font_finding_to_core(f: FontFinding) -> Finding:
    evidence = [
        Evidence(label="token", before="", after=f.token),
        Evidence(label="conflicting_fonts", before=f.context_font, after=f.token_font),
        Evidence(label="bbox", before="", after=_bbox_str(f.bbox)),
    ]
    if f.minority_font:
        # Intra-token seam: surface the suspicious char(s), their glyph index(es),
        # and the foreign minority font (majority -> minority).
        evidence.append(
            Evidence(label="minority_font", before=f.token_font, after=f.minority_font)
        )
    if f.classification_strength is not None:
        evidence.append(
            Evidence(
                label="classification_strength",
                before="",
                after=f.classification_strength.value,
            )
        )
    if f.classification_signals:
        evidence.append(
            Evidence(
                label="classification_signals",
                before="",
                after=", ".join(f.classification_signals),
            )
        )
        evidence.append(
            Evidence(label="suspicious_chars", before="", after=f.suspicious_text)
        )
        evidence.append(
            Evidence(
                label="suspicious_glyph_indexes",
                before="",
                after=",".join(str(i) for i in f.suspicious_glyph_indexes),
            )
        )
    evidence_t = tuple(evidence)
    return Finding(
        stage=STAGE_NAME,
        tier=f.tier,
        reason=f.reason,
        page=f.page_index,
        object_ids=(),  # font forensics is glyph-bound, not object-bound
        before=None,
        after=None,
        high_value=(f.high_value.value if f.high_value else None),
        evidence=evidence_t,
    )


# ---------------------------------------------------------------------------
# FontReport <-> StageResult
# ---------------------------------------------------------------------------

def report_to_stage_result(report: FontReport) -> StageResult:
    """Convert a :class:`FontReport` into a core :class:`StageResult`."""
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

    findings = tuple(_font_finding_to_core(f) for f in report.findings)
    n = len(findings)

    if report.tier is ConfidenceTier.INCONCLUSIVE:
        summary = f"{STAGE_NAME}: inconclusive (single uniform font; route to later stages)"
    else:
        noun = "finding" if n == 1 else "findings"
        score_txt = "n/a" if report.score is None else str(report.score)
        summary = (
            f"{STAGE_NAME}: {report.tier.value.upper()} (score {score_txt}); "
            f"{n} {noun}"
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


def stage_result_to_report(result: StageResult) -> FontReport:
    """Recover the rich :class:`FontReport` carried by a stage result."""
    payload = result.payload
    if not isinstance(payload, FontReport):
        raise TypeError(
            "StageResult.payload is not a FontReport; "
            "cannot render via the font_forensics adapter"
        )
    return payload


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def report_to_dict(report: FontReport) -> dict:
    """Serialise a :class:`FontReport` to a JSON-ready dict (no file bytes)."""
    return {
        "stage": STAGE_NAME,
        "path": report.path,
        "ok": report.ok,
        "error": report.error,
        "raw_size": report.raw_size,
        "page_count": report.page_count,
        "distinct_fonts": list(report.distinct_fonts),
        "tier": report.tier.value,
        "score": report.score,
        "reasons": list(report.reasons),
        "notes": list(report.notes),
        "findings": [
            {
                "page": f.page_index,
                "kind": f.kind.value,
                "tier": f.tier.value,
                "token": f.token,
                "token_font": f.token_font,
                "context_font": f.context_font,
                "conflicting_fonts": list(f.conflicting_fonts),
                "bbox": [round(v, 2) for v in f.bbox],
                "high_value": f.high_value.value if f.high_value else None,
                "classification_strength": (
                    f.classification_strength.value
                    if f.classification_strength is not None
                    else None
                ),
                "classification_candidates": [
                    candidate.value for candidate in f.classification_candidates
                ],
                "classification_signals": list(f.classification_signals),
                "baseline_scope": f.baseline_scope,
                "minority_font": f.minority_font or None,
                "suspicious_chars": f.suspicious_text or None,
                "suspicious_glyph_indexes": list(f.suspicious_glyph_indexes),
                "suspicious_bboxes": [
                    [round(v, 2) for v in b] for b in f.suspicious_bboxes
                ],
                "reason": f.reason,
            }
            for f in report.findings
        ],
    }


def render_json(report: FontReport, *, indent: int = 2) -> str:
    """Render a single report as a JSON object."""
    return json.dumps(report_to_dict(report), indent=indent, ensure_ascii=False)


def render_summary(report: FontReport) -> str:
    """Render a human-readable summary; confidence is ADVISORY."""
    lines: list[str] = []
    lines.append(f"=== font_forensics: {report.path} ===")
    if not report.ok:
        lines.append(f"  ERROR: {report.error}")
        return "\n".join(lines)

    score_txt = "n/a" if report.score is None else str(report.score)
    lines.append(f"  Confidence: {report.tier.value.upper()} (score {score_txt}) — ADVISORY")
    if report.distinct_fonts:
        lines.append(f"  Fonts seen: {', '.join(report.distinct_fonts)}")
    for r in report.reasons:
        lines.append(f"    - {r}")

    if not report.findings:
        lines.append("  No font-inconsistency findings.")
    for i, f in enumerate(report.findings, 1):
        lines.append(
            f"  [{i}] {f.tier.value.upper()} page {f.page_index + 1}: "
            f"token {f.token!r}"
        )
        lines.append(f"        token font : {f.token_font}")
        lines.append(f"        line font  : {f.context_font}")
        lines.append(f"        bbox       : {_bbox_str(f.bbox)}")
        if f.minority_font:
            lines.append(f"        minority   : {f.suspicious_text!r} in {f.minority_font} "
                         f"(glyph index {list(f.suspicious_glyph_indexes)})")
        if f.high_value:
            lines.append(f"        high-value : {f.high_value.value}")
        if f.classification_strength is not None:
            lines.append(
                f"        classify   : {f.classification_strength.value} "
                f"({', '.join(c.value for c in f.classification_candidates)})"
            )
        lines.append(f"        {f.reason}")
    lines.append("  (Confidence is advisory; a human reviewer decides.)")
    return "\n".join(lines)


def render_stage_json(result: StageResult, *, indent: int = 2) -> str:
    """Render a font-forensics :class:`StageResult` as JSON."""
    return render_json(stage_result_to_report(result), indent=indent)


def render_stage_summary(result: StageResult) -> str:
    """Render a font-forensics :class:`StageResult` as a human summary."""
    return render_summary(stage_result_to_report(result))
