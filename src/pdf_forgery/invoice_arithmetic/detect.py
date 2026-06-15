"""Turn broken relationships into localized, convergence-annotated findings.

Orchestrates: reconstructed tables -> evaluated relationships -> broken ones ->
per-finding localization (page / bbox / role / token) + convergence + high-value
tag. High-value classification reuses ``revision_recovery.highvalue`` so amounts
and dates are recognised identically across stages.
"""

from __future__ import annotations

from .config import InvoiceConfig
from .localize import annotate_convergence
from .models import (
    ArithmeticFinding,
    ArithmeticFindingKind,
    Cell,
    ColumnRole,
    ConfidenceTier,
    HighValueKind,
    InvoiceDetectionResult,
    Relationship,
    Table,
)
from .relationships import evaluate_logical_invoice
from .segmentation import segment_logical_invoices
from .summary import build_summary_cells
from .table import build_tables
from ..core.glyphs import Glyph
from ..revision_recovery.extract.normalize import normalize
from ..revision_recovery.highvalue import classify_token_kind


def detect(
    glyphs: list[Glyph], config: InvoiceConfig | None = None
) -> tuple[list[ArithmeticFinding], list[Relationship], list[Table]]:
    """Compatibility wrapper returning the historical three-value tuple."""
    result = detect_detailed(glyphs, config)
    return list(result.findings), list(result.relationships), list(result.tables)


def detect_detailed(
    glyphs: list[Glyph], config: InvoiceConfig | None = None
) -> InvoiceDetectionResult:
    """Reconstruct and evaluate invoice-aware arithmetic with diagnostics."""
    cfg = config or InvoiceConfig()
    tables = build_tables(glyphs, cfg)
    summary_cells = build_summary_cells(glyphs, cfg)
    logical_invoices = segment_logical_invoices(glyphs, tables, summary_cells, cfg)
    relationships: list[Relationship] = []
    suppressed = []
    for invoice in logical_invoices:
        invoice_relationships, invoice_suppressed = evaluate_logical_invoice(invoice, cfg)
        relationships.extend(invoice_relationships)
        suppressed.extend(invoice_suppressed)
    all_tables = [table for invoice in logical_invoices for table in invoice.tables]
    convergence = annotate_convergence(relationships, cfg)

    findings: list[ArithmeticFinding] = []
    for i, rel in enumerate(relationships):
        if rel.within_tolerance:
            continue
        conv_count, corroborators = convergence.get(i, (1, []))
        findings.append(_finding_for(rel, conv_count, corroborators, cfg))

    # Most severe first, then by page / position for a stable reviewer order.
    findings.sort(
        key=lambda f: (_TIER_RANK[f.tier], -f.convergence_count, f.page_index, f.bbox[0])
    )
    return InvoiceDetectionResult(
        findings=tuple(findings),
        relationships=tuple(relationships),
        tables=tuple(all_tables),
        logical_invoices=tuple(logical_invoices),
        suppressed_checks=tuple(suppressed),
    )


_TIER_RANK = {
    ConfidenceTier.HIGH: 0,
    ConfidenceTier.MEDIUM: 1,
    ConfidenceTier.LOW: 2,
    ConfidenceTier.INCONCLUSIVE: 3,
}


def _high_value_of(cell: Cell | None) -> HighValueKind | None:
    if cell is None:
        return None
    text = normalize(cell.text)
    if not text:
        return None
    return classify_token_kind(text)


def _finding_for(
    rel: Relationship,
    conv_count: int,
    corroborators: list[Relationship],
    cfg: InvoiceConfig,
) -> ArithmeticFinding:
    cell = rel.output_cell
    hv = _high_value_of(cell)
    # Amounts are money even when classify_token is conservative; a broken
    # monetary relationship's output is treated as a high-value amount.
    if hv is None and cell is not None and cell.value is not None:
        hv = HighValueKind.AMOUNT

    converged = conv_count >= cfg.convergence_high_threshold
    if converged and cfg.require_convergence_for_high:
        tier = ConfidenceTier.HIGH
    elif rel.is_gross:
        tier = ConfidenceTier.MEDIUM
    else:
        tier = ConfidenceTier.MEDIUM

    reason = _reason(rel, conv_count, corroborators, tier)
    return ArithmeticFinding(
        page_index=rel.page_index,
        kind=ArithmeticFindingKind.BROKEN_RELATIONSHIP,
        tier=tier,
        relationship_kind=rel.kind,
        equation_text=rel.equation_text,
        expected=rel.expected,
        stated=rel.stated,
        delta=rel.delta,
        is_gross=rel.is_gross,
        reason=reason,
        cell_role=(cell.role if cell is not None else ColumnRole.UNKNOWN),
        cell_text=(cell.text if cell is not None else ""),
        bbox=(cell.bbox if cell is not None else (0.0, 0.0, 0.0, 0.0)),
        high_value=hv,
        convergence_count=conv_count,
        corroborating=tuple(c.equation_text for c in corroborators),
        logical_invoice_id=rel.logical_invoice_id,
        segmentation_confidence=rel.segmentation_confidence,
        segmentation_basis=rel.segmentation_basis,
        role_label=rel.role_label,
        equation_kind=rel.equation_kind or rel.kind.value,
    )


def _reason(
    rel: Relationship,
    conv_count: int,
    corroborators: list[Relationship],
    tier: ConfidenceTier,
) -> str:
    grossness = "gross " if rel.is_gross else ""
    base = f"{grossness}arithmetic mismatch: {rel.equation_text} (delta {rel.delta:+.2f})"
    if conv_count >= 2:
        return (
            base
            + f"; convergence: correcting this cell reconciles {conv_count} "
            + "independent equations"
        )
    return base + "; lone broken equation (no corroboration — could be source/extraction error)"
