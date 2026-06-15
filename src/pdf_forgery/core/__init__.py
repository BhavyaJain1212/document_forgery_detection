"""Stage-agnostic core for the multi-stage forgery-detection pipeline.

Defines the shared vocabulary every detection stage speaks so their outputs can
be collected and (later) fused:

    - :mod:`~pdf_forgery.core.types`   — ConfidenceTier, Evidence, Finding, StageResult
    - :mod:`~pdf_forgery.core.stage`   — the Stage protocol
    - :mod:`~pdf_forgery.core.context` — AnalysisContext (shared, cached artifacts)

Stages depend on ``core``; ``core`` depends on no stage.
"""

from .context import AnalysisContext
from .stage import Stage
from .types import ConfidenceTier, Evidence, Finding, StageResult

__all__ = [
    "AnalysisContext",
    "ConfidenceTier",
    "Evidence",
    "Finding",
    "Stage",
    "StageResult",
]
