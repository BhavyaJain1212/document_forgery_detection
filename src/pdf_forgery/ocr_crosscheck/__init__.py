"""Stage 3 — OCR ↔ embedded-text divergence (cross-check layer).

Compares the deterministic embedded text layer (pdfminer.six) against text
recovered from the rendered raster (pypdfium2 → PaddleOCR) to detect divergence
between "what the PDF claims" and "what actually renders" — catching image-layer
overlays, hybrid layer mixing, and hidden/invisible text that the structural
stages cannot see.

DESIGN CONTRACT + STUBS only (Session 3.0). Data models / config are real; all
detection logic raises ``NotImplementedError`` (lands in 3.1 / 3.2). The full
design is in ``docs/STAGE3_DESIGN.md``. This subpackage is a sibling of
``revision_recovery`` / ``font_forensics`` / ``invoice_arithmetic`` /
``provenance_metadata`` and a SUBSTANTIVE fusion stage.
"""

from __future__ import annotations

from .adapter import STAGE_NAME, render_stage_json, render_stage_summary
from .analyze import analyze_bytes, analyze_path
from .config import OCRCrossCheckConfig
from .models import (
    Divergence,
    DivergenceType,
    OCRCrossCheckReport,
    RenderProvenance,
    Stage3Result,
    TokenClass,
    WordBox,
    WordSource,
)
from .ocr_engine import OCREngine, PaddleOCREngine
from .stage import OCRCrossCheckStage

__all__ = [
    "STAGE_NAME",
    "OCRCrossCheckStage",
    "OCRCrossCheckConfig",
    "analyze_bytes",
    "analyze_path",
    "render_stage_json",
    "render_stage_summary",
    "OCRCrossCheckReport",
    "Stage3Result",
    "Divergence",
    "DivergenceType",
    "TokenClass",
    "WordBox",
    "WordSource",
    "RenderProvenance",
    "OCREngine",
    "PaddleOCREngine",
]
