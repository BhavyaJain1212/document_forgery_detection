"""Invoice arithmetic packaged as a pipeline :class:`Stage`.

Reads the bill like an accountant: reconstructs the table from glyph
coordinates, then flags numbers whose labelled relationships (qty*rate=amount,
sums, taxes, deposit+balance, ...) no longer hold. Targets the clean-re-render
edit (e.g. Sejda) that font / revision-recovery stages cannot catch — uniform
fonts, single revision, no structural seam — where a broken qty*rate=amount is
the only signal left.

Consumes the shared :class:`~pdf_forgery.core.context.AnalysisContext` page
layouts so the file is parsed once across all stages.
"""

from __future__ import annotations

from ..core.context import AnalysisContext
from ..core.types import StageResult
from .adapter import STAGE_NAME, report_to_stage_result
from .analyze import analyze_bytes
from .config import InvoiceConfig


class InvoiceArithmeticStage:
    """Stage: flag numbers whose labelled accounting relationships break."""

    name = STAGE_NAME

    def __init__(self, config: InvoiceConfig | None = None) -> None:
        self._config = config

    def run(self, pdf_bytes: bytes, ctx: AnalysisContext) -> StageResult:
        """Analyse the bytes and return a :class:`StageResult` (never raises)."""
        path = ctx.path or "<bytes>"
        report = analyze_bytes(pdf_bytes, path, self._config, ctx=ctx)
        return report_to_stage_result(report)
