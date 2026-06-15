"""The :class:`Stage` protocol every detection stage implements.

A stage is anything that, given the raw PDF bytes and a shared
:class:`~pdf_forgery.core.context.AnalysisContext`, produces a
:class:`~pdf_forgery.core.types.StageResult`. Keeping this a ``Protocol`` (rather
than an ABC) means a stage need only provide a ``name`` and a ``run`` method —
it does not have to inherit from anything, so existing modules can be adapted
into stages with minimal coupling.

The ``ctx`` argument exists so stages share already-parsed artifacts (pikepdf
doc, pdfminer layouts, rasterized pages) instead of each re-parsing the file.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .context import AnalysisContext
from .types import StageResult


@runtime_checkable
class Stage(Protocol):
    """A single detection stage in the forgery-detection pipeline."""

    name: str
    """Stable identifier for this stage (also stamped onto its findings)."""

    def run(self, pdf_bytes: bytes, ctx: AnalysisContext) -> StageResult:
        """Analyse ``pdf_bytes`` and return a :class:`StageResult`.

        Implementations MUST be read-only with respect to the input and MUST NOT
        raise: a processing failure is reported as a ``StageResult`` with
        ``ok=False`` and an ``error`` (mirroring "report and continue, never
        crash"). Shared artifacts should be obtained from ``ctx`` so the file is
        parsed once across all stages.
        """
        ...
