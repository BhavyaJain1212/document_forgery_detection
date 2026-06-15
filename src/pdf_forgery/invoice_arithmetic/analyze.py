"""Orchestration: glyph extraction -> table -> relationships -> InvoiceReport.

``analyze_bytes`` / ``analyze_path`` return the rich :class:`InvoiceReport`;
``analyze_bytes_as_stage`` / ``analyze_path_as_stage`` return a core
:class:`~pdf_forgery.core.types.StageResult` via the adapter. None of these ever
raise — a processing failure becomes an ``ok=False`` report.

When a shared :class:`~pdf_forgery.core.context.AnalysisContext` is available the
stage consumes its cached ``page_layouts`` (so the file is parsed once across all
stages); otherwise it extracts glyphs straight from the bytes.
"""

from __future__ import annotations

from pathlib import Path

from ..core.context import AnalysisContext
from ..core.glyphs import Glyph, glyphs_from_bytes, glyphs_from_layouts
from ..core.types import StageResult
from .config import InvoiceConfig
from .detect import detect_detailed
from .models import ConfidenceTier, InvoiceReport
from .scoring import score


def analyze_bytes(
    raw: bytes,
    path: str = "<bytes>",
    config: InvoiceConfig | None = None,
    *,
    ctx: AnalysisContext | None = None,
) -> InvoiceReport:
    """Analyse PDF *raw* bytes and return an :class:`InvoiceReport` (never raises)."""
    cfg = config or InvoiceConfig()
    notes: list[str] = []

    try:
        glyphs, page_count = _extract(raw, ctx)
    except Exception as exc:  # extraction must never abort the stage
        return InvoiceReport(
            path=path, ok=False, tier=ConfidenceTier.INCONCLUSIVE, score=None,
            error=f"glyph extraction failed: {exc}", raw_size=len(raw),
        )

    if not glyphs:
        notes.append("no extractable text glyphs (image-only, empty, or encrypted)")

    detection = detect_detailed(glyphs, cfg)
    findings = list(detection.findings)
    relationships = list(detection.relationships)
    tables = list(detection.tables)
    numeric_cells = sum(
        1 for t in tables for r in t.rows for c in r.cells if c.is_numeric
    )
    table_found = bool(tables)
    tier, score_val, reasons = score(
        findings, relationships, numeric_cells, table_found, cfg
    )

    if relationships:
        reconciled = sum(1 for r in relationships if r.within_tolerance)
        notes.append(
            f"evaluated {len(relationships)} relationship(s); "
            f"{reconciled} reconciled, {len(relationships) - reconciled} broken"
        )
    if detection.suppressed_checks:
        notes.append(
            f"suppressed {len(detection.suppressed_checks)} invoice-level "
            "equation(s) because ownership/segmentation was not reliable"
        )

    return InvoiceReport(
        path=path,
        ok=True,
        tier=tier,
        score=score_val,
        page_count=page_count,
        table_found=table_found,
        numeric_cell_count=numeric_cells,
        relationships=tuple(relationships),
        findings=tuple(findings),
        logical_invoices=detection.logical_invoices,
        suppressed_checks=detection.suppressed_checks,
        reasons=tuple(reasons),
        notes=tuple(notes),
        error=None,
        raw_size=len(raw),
        _tables=tuple(tables),
    )


def analyze_path(path: str | Path, config: InvoiceConfig | None = None) -> InvoiceReport:
    """Read a PDF file (read-only) and analyse it; missing/dir -> ok=False."""
    p = Path(path)
    try:
        if not p.exists():
            return _failed_report(str(path), "file not found")
        if p.is_dir():
            return _failed_report(str(path), "path is a directory, not a PDF file")
        raw = p.read_bytes()
    except OSError as exc:
        return _failed_report(str(path), f"could not read file: {exc}")
    return analyze_bytes(raw, str(path), config)


# ---------------------------------------------------------------------------
# Stage-schema variants
# ---------------------------------------------------------------------------

def analyze_bytes_as_stage(
    raw: bytes,
    path: str = "<bytes>",
    config: InvoiceConfig | None = None,
    *,
    ctx: AnalysisContext | None = None,
) -> StageResult:
    """Analyse bytes and return a core :class:`StageResult`."""
    from .adapter import report_to_stage_result

    return report_to_stage_result(analyze_bytes(raw, path, config, ctx=ctx))


def analyze_path_as_stage(
    path: str | Path, config: InvoiceConfig | None = None
) -> StageResult:
    """Analyse a file and return a core :class:`StageResult`."""
    from .adapter import report_to_stage_result

    return report_to_stage_result(analyze_path(path, config))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _extract(raw: bytes, ctx: AnalysisContext | None) -> tuple[list[Glyph], int]:
    """Extract glyphs, preferring the shared context's cached layouts."""
    if ctx is not None:
        layouts = ctx.page_layouts
        if layouts:
            return glyphs_from_layouts(layouts), len(layouts)
    glyphs = glyphs_from_bytes(raw)
    page_count = 1 + max((g.page_index for g in glyphs), default=-1)
    return glyphs, page_count


def _failed_report(path: str, error: str) -> InvoiceReport:
    return InvoiceReport(
        path=path, ok=False, tier=ConfidenceTier.INCONCLUSIVE, score=None,
        error=error, notes=(error,),
    )
