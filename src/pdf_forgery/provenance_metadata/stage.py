"""Provenance metadata packaged as a pipeline :class:`Stage`.

Cheap corroboration: a "hospital bill" whose /Producer is a consumer web PDF
editor (or a bare version string from a re-render tool), or whose ModDate is
after its CreationDate, is suspicious. Reads ONLY the Info dictionary, XMP, and
trailer /ID — never /Prev pointers or xref walking (that is revision_recovery's
job). NEVER reaches HIGH on its own.

Consumes the shared :class:`~pdf_forgery.core.context.AnalysisContext` pikepdf
document so the file is opened once across all stages.
"""

from __future__ import annotations

from ..core.context import AnalysisContext
from ..core.types import StageResult
from .adapter import STAGE_NAME, report_to_stage_result
from .analyze import analyze_bytes
from .config import ProvenanceConfig


class ProvenanceMetadataStage:
    """Stage: corroborate a forgery case from document provenance metadata."""

    name = STAGE_NAME

    def __init__(self, config: ProvenanceConfig | None = None) -> None:
        self._config = config

    def run(self, pdf_bytes: bytes, ctx: AnalysisContext) -> StageResult:
        """Analyse the bytes and return a :class:`StageResult` (never raises)."""
        path = ctx.path or "<bytes>"
        report = analyze_bytes(pdf_bytes, path, self._config, ctx=ctx)
        return report_to_stage_result(report)
