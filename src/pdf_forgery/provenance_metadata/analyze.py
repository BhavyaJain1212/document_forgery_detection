"""Orchestration: read metadata -> detect -> score -> ProvenanceReport.

``analyze_bytes`` / ``analyze_path`` return the rich :class:`ProvenanceReport`;
``analyze_bytes_as_stage`` / ``analyze_path_as_stage`` return a core
:class:`~pdf_forgery.core.types.StageResult` via the adapter. None ever raise —
a failure becomes an ``ok=False`` report.

Uses the shared :class:`~pdf_forgery.core.context.AnalysisContext`'s cached
pikepdf document when available, so the file is opened once across all stages.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from ..core.context import AnalysisContext
from ..core.types import StageResult
from .config import ProvenanceConfig
from .detect import Metadata, detect, read_metadata
from .models import ConfidenceTier, ProvenanceReport
from .scoring import score


def analyze_bytes(
    raw: bytes,
    path: str = "<bytes>",
    config: ProvenanceConfig | None = None,
    *,
    ctx: AnalysisContext | None = None,
) -> ProvenanceReport:
    """Analyse PDF *raw* bytes and return a :class:`ProvenanceReport` (never raises)."""
    cfg = config or ProvenanceConfig()

    pdf, opened_here = _open(raw, ctx)
    if pdf is None:
        return ProvenanceReport(
            path=path, ok=True, tier=ConfidenceTier.INCONCLUSIVE, score=None,
            notes=("could not open the PDF to read metadata (corrupt/encrypted)",),
            raw_size=len(raw),
        )

    try:
        meta = read_metadata(pdf)
    except Exception as exc:  # metadata read must never abort the stage
        return ProvenanceReport(
            path=path, ok=False, tier=ConfidenceTier.INCONCLUSIVE, score=None,
            error=f"metadata read failed: {exc}", raw_size=len(raw),
        )
    finally:
        if opened_here:
            try:
                pdf.close()
            except Exception:
                pass

    metadata_readable = _has_any_metadata(meta)
    findings = detect(meta, cfg)
    tier, score_val, reasons = score(findings, metadata_readable, cfg)

    return ProvenanceReport(
        path=path,
        ok=True,
        tier=tier,
        score=score_val,
        producer=meta.producer,
        creator=meta.creator,
        creation_date=meta.creation_date,
        mod_date=meta.mod_date,
        xmp_producer=meta.xmp_producer,
        id_pair=meta.id_pair,
        findings=tuple(findings),
        reasons=tuple(reasons),
        notes=(),
        error=None,
        raw_size=len(raw),
    )


def analyze_path(
    path: str | Path, config: ProvenanceConfig | None = None
) -> ProvenanceReport:
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
    config: ProvenanceConfig | None = None,
    *,
    ctx: AnalysisContext | None = None,
) -> StageResult:
    from .adapter import report_to_stage_result

    return report_to_stage_result(analyze_bytes(raw, path, config, ctx=ctx))


def analyze_path_as_stage(
    path: str | Path, config: ProvenanceConfig | None = None
) -> StageResult:
    from .adapter import report_to_stage_result

    return report_to_stage_result(analyze_path(path, config))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _open(raw: bytes, ctx: AnalysisContext | None):
    """Return ``(pdf, opened_here)``; prefer the shared context's open handle."""
    if ctx is not None:
        doc = ctx.pikepdf_doc
        if doc is not None:
            return doc, False
    try:
        import pikepdf

        return pikepdf.open(BytesIO(raw)), True
    except Exception:
        return None, False


def _has_any_metadata(meta: Metadata) -> bool:
    return any(
        v is not None
        for v in (
            meta.producer, meta.creator, meta.creation_date,
            meta.mod_date, meta.xmp_producer, meta.id_pair,
        )
    )


def _failed_report(path: str, error: str) -> ProvenanceReport:
    return ProvenanceReport(
        path=path, ok=False, tier=ConfidenceTier.INCONCLUSIVE, score=None,
        error=error, notes=(error,),
    )
