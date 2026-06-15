"""Revision recovery packaged as a pipeline :class:`Stage`.

Wraps the existing Stage 1 analysis (``analyze_bytes`` -> ``AnalysisReport``) and
maps it onto the core stage schema via the adapter, so the orchestrator can run
it alongside future stages (font fingerprinting, OCR cross-check) without knowing
its internals. Detection logic and scoring are unchanged.
"""

from __future__ import annotations

from ..core.context import AnalysisContext
from ..core.types import StageResult
from .adapter import STAGE_NAME, report_to_stage_result
from .analyze import analyze_bytes
from .config import Config


class RevisionRecoveryStage:
    """Stage 1: detect direct-text-editing forgery via revision recovery."""

    name = STAGE_NAME

    def __init__(self, config: Config | None = None) -> None:
        self._config = config

    def run(self, pdf_bytes: bytes, ctx: AnalysisContext) -> StageResult:
        """Analyse the bytes and return a :class:`StageResult` (never raises)."""
        path = ctx.path or "<bytes>"
        report = analyze_bytes(pdf_bytes, path, self._config)
        return report_to_stage_result(report)
