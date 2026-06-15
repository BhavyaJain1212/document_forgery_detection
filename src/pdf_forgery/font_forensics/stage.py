"""Font forensics packaged as a pipeline :class:`Stage`.

Stage 2: detect text that was edited before the PDF was flattened, by spotting
per-character font / subset inconsistencies. Complements revision recovery —
it is the stage that can still find an edit in a SINGLE-revision PDF (where
revision recovery returns INCONCLUSIVE).

Consumes the shared :class:`~pdf_forgery.core.context.AnalysisContext` page
layouts so the file is parsed once across all stages.
"""

from __future__ import annotations

from ..core.context import AnalysisContext
from ..core.types import StageResult
from .adapter import STAGE_NAME, report_to_stage_result
from .analyze import analyze_bytes
from .config import FontConfig


class FontForensicsStage:
    """Stage 2: detect pre-flatten text edits via font/subset inconsistency."""

    name = STAGE_NAME

    def __init__(self, config: FontConfig | None = None) -> None:
        self._config = config

    def run(self, pdf_bytes: bytes, ctx: AnalysisContext) -> StageResult:
        """Analyse the bytes and return a :class:`StageResult` (never raises)."""
        path = ctx.path or "<bytes>"
        report = analyze_bytes(pdf_bytes, path, self._config, ctx=ctx)
        return report_to_stage_result(report)
