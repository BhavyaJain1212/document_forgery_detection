"""Adapter between the rich :class:`InvoiceReport` and the core stage schema.

Maps an invoice-arithmetic report onto the shared
:class:`~pdf_forgery.core.types.StageResult` (carrying the report as ``payload``)
and provides JSON / human-summary renderers. Mirrors ``font_forensics.adapter``
and ``revision_recovery.adapter`` so the orchestrator treats stages uniformly.
"""

from __future__ import annotations

import json

from ..core.types import ConfidenceTier, Evidence, Finding, StageResult
from .models import ArithmeticFinding, InvoiceReport

STAGE_NAME = "invoice_arithmetic"


# ---------------------------------------------------------------------------
# ArithmeticFinding -> core Finding
# ---------------------------------------------------------------------------

def _bbox_str(bbox: tuple[float, float, float, float]) -> str:
    return ", ".join(f"{v:.1f}" for v in bbox)


def _finding_to_core(f: ArithmeticFinding) -> Finding:
    evidence = [
        Evidence(label="equation", before="", after=f.equation_text),
        Evidence(label="expected", before="", after=f"{f.expected}"),
        Evidence(label="stated", before="", after=f"{f.stated}"),
        Evidence(label="delta", before="", after=f"{f.delta:+.2f}"),
        Evidence(label="cell_role", before="", after=f.cell_role.value),
        Evidence(label="cell_text", before="", after=f.cell_text),
        Evidence(label="bbox", before="", after=_bbox_str(f.bbox)),
        Evidence(label="convergence", before="", after=str(f.convergence_count)),
        Evidence(label="logical_invoice_id", before="", after=f.logical_invoice_id),
        Evidence(label="segmentation_confidence", before="", after=f.segmentation_confidence.value),
        Evidence(label="segmentation_basis", before="", after="; ".join(f.segmentation_basis)),
        Evidence(label="page_number", before="", after=str(f.page_number)),
        Evidence(label="role_label", before="", after=f.role_label),
        Evidence(label="equation_kind", before="", after=f.equation_kind),
    ]
    for corr in f.corroborating:
        evidence.append(Evidence(label="corroborating_equation", before="", after=corr))
    # Headline before -> after: stated (suspect) vs expected (what the math says).
    return Finding(
        stage=STAGE_NAME,
        tier=f.tier,
        reason=f.reason,
        page=f.page_index,
        object_ids=(),  # arithmetic findings are geometry/cell-bound, not object-bound
        before=f"{f.stated}",
        after=f"{f.expected}",
        high_value=(f.high_value.value if f.high_value else None),
        evidence=tuple(evidence),
    )


# ---------------------------------------------------------------------------
# InvoiceReport <-> StageResult
# ---------------------------------------------------------------------------

def report_to_stage_result(report: InvoiceReport) -> StageResult:
    """Convert an :class:`InvoiceReport` into a core :class:`StageResult`."""
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
        summary = (
            f"{STAGE_NAME}: inconclusive (table/roles not identified; "
            "route to later stages)"
        )
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


def stage_result_to_report(result: StageResult) -> InvoiceReport:
    """Recover the rich :class:`InvoiceReport` carried by a stage result."""
    payload = result.payload
    if not isinstance(payload, InvoiceReport):
        raise TypeError(
            "StageResult.payload is not an InvoiceReport; "
            "cannot render via the invoice_arithmetic adapter"
        )
    return payload


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def report_to_dict(report: InvoiceReport) -> dict:
    """Serialise an :class:`InvoiceReport` to a JSON-ready dict (no file bytes)."""
    return {
        "stage": STAGE_NAME,
        "path": report.path,
        "ok": report.ok,
        "error": report.error,
        "raw_size": report.raw_size,
        "page_count": report.page_count,
        "table_found": report.table_found,
        "numeric_cell_count": report.numeric_cell_count,
        "tier": report.tier.value,
        "score": report.score,
        "reasons": list(report.reasons),
        "notes": list(report.notes),
        "logical_invoices": [
            {
                "logical_invoice_id": invoice.logical_invoice_id,
                "page_indexes": list(invoice.page_indexes),
                "page_numbers": list(invoice.page_numbers),
                "segmentation_confidence": invoice.segmentation_confidence.value,
                "segmentation_basis": list(invoice.segmentation_basis),
                "allow_cross_row_checks": invoice.allow_cross_row_checks,
                "table_count": len(invoice.tables),
            }
            for invoice in report.logical_invoices
        ],
        "suppressed_checks": [
            {
                "kind": check.kind.value,
                "logical_invoice_id": check.logical_invoice_id,
                "page_indexes": list(check.page_indexes),
                "page_numbers": list(check.page_numbers),
                "reason": check.reason,
                "segmentation_confidence": check.segmentation_confidence.value,
                "segmentation_basis": list(check.segmentation_basis),
            }
            for check in report.suppressed_checks
        ],
        "relationships": [
            {
                "kind": r.kind.value,
                "page": r.page_index,
                "page_number": r.page_number,
                "equation": r.equation_text,
                "equation_kind": r.equation_kind or r.kind.value,
                "expected": r.expected,
                "stated": r.stated,
                "delta": r.delta,
                "within_tolerance": r.within_tolerance,
                "is_gross": r.is_gross,
                "bbox": [round(v, 2) for v in r.bbox],
                "role_label": r.role_label,
                "logical_invoice_id": r.logical_invoice_id,
                "segmentation_confidence": r.segmentation_confidence.value,
                "segmentation_basis": list(r.segmentation_basis),
            }
            for r in report.relationships
        ],
        "findings": [
            {
                "page": f.page_index,
                "page_number": f.page_number,
                "tier": f.tier.value,
                "relationship_kind": f.relationship_kind.value,
                "equation": f.equation_text,
                "equation_kind": f.equation_kind or f.relationship_kind.value,
                "expected": f.expected,
                "stated": f.stated,
                "delta": f.delta,
                "is_gross": f.is_gross,
                "cell_role": f.cell_role.value,
                "role_label": f.role_label,
                "cell_text": f.cell_text,
                "bbox": [round(v, 2) for v in f.bbox],
                "high_value": f.high_value.value if f.high_value else None,
                "convergence_count": f.convergence_count,
                "corroborating": list(f.corroborating),
                "reason": f.reason,
                "logical_invoice_id": f.logical_invoice_id,
                "segmentation_confidence": f.segmentation_confidence.value,
                "segmentation_basis": list(f.segmentation_basis),
            }
            for f in report.findings
        ],
    }


def render_json(report: InvoiceReport, *, indent: int = 2) -> str:
    """Render a single report as a JSON object."""
    return json.dumps(report_to_dict(report), indent=indent, ensure_ascii=False)


def render_summary(report: InvoiceReport) -> str:
    """Render a human-readable summary; confidence is ADVISORY."""
    lines: list[str] = []
    lines.append(f"=== invoice_arithmetic: {report.path} ===")
    if not report.ok:
        lines.append(f"  ERROR: {report.error}")
        return "\n".join(lines)

    score_txt = "n/a" if report.score is None else str(report.score)
    lines.append(
        f"  Confidence: {report.tier.value.upper()} (score {score_txt}) — ADVISORY"
    )
    lines.append(
        f"  Tables: {'yes' if report.table_found else 'no'}; "
        f"numeric cells: {report.numeric_cell_count}; "
        f"relationships evaluated: {len(report.relationships)}"
    )
    lines.append(
        f"  Logical invoices: {len(report.logical_invoices)}; "
        f"suppressed invoice-level checks: {len(report.suppressed_checks)}"
    )
    for r in report.reasons:
        lines.append(f"    - {r}")

    if not report.findings:
        lines.append("  No broken arithmetic relationships.")
    for i, f in enumerate(report.findings, 1):
        lines.append(
            f"  [{i}] {f.tier.value.upper()} page {f.page_index + 1}: "
            f"{f.relationship_kind.value} ({f.logical_invoice_id})"
        )
        lines.append(f"        {f.equation_text}")
        lines.append(
            f"        expected {f.expected}  stated {f.stated}  (delta {f.delta:+.2f})"
        )
        lines.append(
            f"        localized cell: role={f.cell_role.value} text={f.cell_text!r} "
            f"bbox=({_bbox_str(f.bbox)})"
        )
        if f.high_value:
            lines.append(f"        high-value : {f.high_value.value}")
        lines.append(f"        convergence: {f.convergence_count}")
        for corr in f.corroborating:
            lines.append(f"          corroborated by: {corr}")
    for check in report.suppressed_checks:
        lines.append(
            f"  SUPPRESSED {check.kind.value} for {check.logical_invoice_id}: "
            f"{check.reason}"
        )
    lines.append("  (Confidence is advisory; a human reviewer decides.)")
    return "\n".join(lines)


def render_stage_json(result: StageResult, *, indent: int = 2) -> str:
    """Render an invoice-arithmetic :class:`StageResult` as JSON."""
    return render_json(stage_result_to_report(result), indent=indent)


def render_stage_summary(result: StageResult) -> str:
    """Render an invoice-arithmetic :class:`StageResult` as a human summary."""
    return render_summary(stage_result_to_report(result))
