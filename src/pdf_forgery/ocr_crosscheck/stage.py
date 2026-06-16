"""OCR cross-check packaged as a pipeline :class:`Stage` (Stage 3).

Conforms to ``core.Stage`` (``name`` + ``run(pdf_bytes, ctx) -> StageResult``) so
the orchestrator can run it alongside the structural stages. It compares the
embedded text layer against OCR of the rendered raster to catch image-layer
overlays / hybrid mixing / hidden text — the classes the structural stages
cannot see.

DESIGN-ONLY this session: ``run`` delegates to the stub ``analyze_bytes`` /
adapter, which raise ``NotImplementedError``. The stage is intentionally NOT
registered in the live ``STAGES`` list yet (see ``docs/STAGE3_DESIGN.md`` §0),
so this raise cannot fire in a real pipeline run until 3.2 wires it in.
"""

from __future__ import annotations

from ..core.context import AnalysisContext
from ..core.types import StageResult
from .adapter import STAGE_NAME, report_to_stage_result
from .analyze import analyze_bytes
from .config import OCRCrossCheckConfig
from .ocr_engine import OCREngine


class OCRCrossCheckStage:
    """Stage 3: detect render-vs-text divergence via OCR cross-check."""

    name = STAGE_NAME

    def __init__(self, config: OCRCrossCheckConfig | None = None,
                 *, engine: OCREngine | None = None) -> None:
        self._config = config
        self._engine = engine

    def run(self, pdf_bytes: bytes, ctx: AnalysisContext) -> StageResult:
        """Analyse the bytes and return a :class:`StageResult` (never raises once
        implemented). Stub delegates to the not-yet-implemented analysis."""
        path = ctx.path or "<bytes>"
        report = analyze_bytes(pdf_bytes, path, self._config, ctx=ctx, engine=self._engine)
        return report_to_stage_result(report)


__all__ = ["OCRCrossCheckStage"]
