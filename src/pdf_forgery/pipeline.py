"""Top-level orchestrator: run a list of detection stages over one PDF.

Builds a single shared :class:`~pdf_forgery.core.context.AnalysisContext` (so the
file is parsed at most once per artifact), runs each :class:`Stage` against it,
and collects their :class:`~pdf_forgery.core.types.StageResult` objects.

Fusion of the per-stage results into one combined verdict/report is a LATER
concern — for now the orchestrator simply returns the list of results in stage
order. A stage that fails to run still returns a ``StageResult`` (``ok=False``),
so one stage erroring never aborts the others.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .core.context import AnalysisContext
from .core.stage import Stage
from .core.types import ConfidenceTier, StageResult


def run_pipeline(
    pdf_bytes: bytes,
    stages: Sequence[Stage],
    *,
    path: str | None = None,
) -> list[StageResult]:
    """Run ``stages`` over ``pdf_bytes`` and return their results in order.

    A shared :class:`AnalysisContext` is created once and passed to every stage.
    Stages are expected not to raise (per the :class:`Stage` contract); should one
    raise anyway, the failure is captured as an ``ok=False`` :class:`StageResult`
    rather than aborting the run.
    """
    results: list[StageResult] = []
    with AnalysisContext(pdf_bytes, path=path) as ctx:
        for stage in stages:
            results.append(_run_one(stage, pdf_bytes, ctx))
    return results


def run_pipeline_on_path(
    path: str | Path,
    stages: Sequence[Stage],
) -> list[StageResult]:
    """Read a PDF file (read-only) and run the pipeline over it.

    A missing / unreadable / non-file path yields one ``ok=False``
    :class:`StageResult` per stage rather than raising.
    """
    p = Path(path)
    try:
        if not p.exists():
            return _failed_all(stages, str(path), "file not found")
        if p.is_dir():
            return _failed_all(stages, str(path), "path is a directory, not a PDF file")
        raw = p.read_bytes()
    except OSError as exc:
        return _failed_all(stages, str(path), f"could not read file: {exc}")
    return run_pipeline(raw, stages, path=str(path))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _run_one(stage: Stage, pdf_bytes: bytes, ctx: AnalysisContext) -> StageResult:
    """Run a single stage, converting an unexpected raise into an ok=False result."""
    try:
        return stage.run(pdf_bytes, ctx)
    except Exception as exc:  # the Stage contract says never raise; be defensive
        return _stage_error(_stage_name(stage), f"stage raised: {exc}")


def _failed_all(stages: Sequence[Stage], path: str, error: str) -> list[StageResult]:
    return [_stage_error(_stage_name(s), error) for s in stages]


def _stage_error(name: str, error: str) -> StageResult:
    return StageResult(
        stage=name,
        tier=ConfidenceTier.INCONCLUSIVE,
        score=None,
        findings=(),
        summary=f"{name}: did not run ({error})",
        reasons=(),
        notes=(error,),
        ok=False,
        error=error,
    )


def _stage_name(stage: Stage) -> str:
    return getattr(stage, "name", stage.__class__.__name__)
