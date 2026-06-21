"""Stage 6 packaged as a pipeline :class:`Stage` (raster / pixel forensics).

Conforms to ``core.Stage`` (``name`` + ``run(pdf_bytes, ctx) -> StageResult``) so
the orchestrator runs it alongside the parse-side stages. The flow is the 6.1–6.3
pipeline: activation (which pages are image-dominant) → detection (run the engine,
localize + combine fires) → scoring (§7 rule tree) → adapt to a core
:class:`StageResult` (the rich :class:`ImageForensicsReport` is carried as
``payload`` so its bbox-bearing region findings are available downstream).

**Fusion role: SUBSTANTIVE.** Stage 6 can independently originate a verdict on a
scanned forgery that the parse-side stages (1–4) return INCONCLUSIVE on, so it is
NOT added to ``FusionConfig.corroborator_stages``. On a digital-native document it
returns INCONCLUSIVE (no image-dominant page) and therefore never drags down a
parse-side HIGH.

**Live-safe while the DSP is deferred (6.2 decision):** the default provider is
the classical one, whose methods still raise ``NotImplementedError`` — recorded by
:mod:`.detect` as *capability gaps*, which the scorer treats as no signal. So the
stage is **INCONCLUSIVE on every document** until the real pixel math lands; it can
be registered in the live pipeline without manufacturing false MEDIUMs. Inject a
provider (tests) to exercise the real rule tree.

Never raises: any internal failure becomes ``ok=False`` (the ``Stage`` contract).
"""

from __future__ import annotations

from ..core.context import AnalysisContext
from ..core.types import ConfidenceTier, Finding, StageResult
from .activation import activate
from .config import ImageForensicsConfig
from .detect import detect
from .engine import ForensicProvider
from .scoring import ImageForensicsReport, RegionFinding, score

STAGE_NAME = "image_forensics"


class ImageForensicsStage:
    """Stage 6: localized pixel-tamper detection on image-dominant (scanned) pages."""

    name = STAGE_NAME

    def __init__(
        self,
        config: ImageForensicsConfig | None = None,
        *,
        provider: ForensicProvider | None = None,
    ) -> None:
        self._config = config
        self._provider = provider

    def run(self, pdf_bytes: bytes, ctx: AnalysisContext) -> StageResult:
        try:
            cfg = self._config or ImageForensicsConfig()
            activation = activate(ctx, cfg)
            detection = detect(
                ctx, provider=self._provider, config=cfg, activation=activation
            )
            report = score(detection, activation, cfg)
            return report_to_stage_result(report)
        except Exception as exc:  # the Stage contract: never raise
            return StageResult(
                stage=STAGE_NAME,
                tier=ConfidenceTier.INCONCLUSIVE,
                score=None,
                findings=(),
                summary=f"{STAGE_NAME}: did not run ({type(exc).__name__})",
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )


# --------------------------------------------------------------------------- #
# Adapter: rich report -> core StageResult
# --------------------------------------------------------------------------- #

def _region_finding_to_finding(rf: RegionFinding) -> Finding:
    """Map one scored region to a core :class:`Finding`.

    Stage 6 findings carry NO before/after text (there is no text — only pixels);
    the geometry lives on the rich payload region (``rf.region.page_bbox``), which
    is already in ``revision_recovery``'s ``FindingLocation`` point convention.
    The PHI-safe high-value tag is positional only (§4), labelled ``"amount"``.
    """
    r = rf.region
    return Finding(
        stage=STAGE_NAME,
        tier=rf.tier,
        reason=rf.reason,
        page=r.page_index,
        object_ids=(),
        before=None,
        after=None,
        high_value="amount" if r.high_value else None,
    )


def report_to_stage_result(report: ImageForensicsReport) -> StageResult:
    """Convert an :class:`ImageForensicsReport` into a core :class:`StageResult`."""
    if not report.ok:
        return StageResult(
            stage=STAGE_NAME,
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            findings=(),
            summary=f"{STAGE_NAME}: could not analyse file ({report.error})",
            notes=report.notes,
            ok=False,
            error=report.error,
            payload=report,
        )

    findings = tuple(_region_finding_to_finding(rf) for rf in report.findings)
    return StageResult(
        stage=STAGE_NAME,
        tier=report.tier,
        score=report.score,
        findings=findings,
        summary=report.summary,
        reasons=report.reasons,
        notes=report.notes,
        ok=True,
        error=None,
        payload=report,
    )


def stage_result_to_report(result: StageResult) -> ImageForensicsReport:
    """Recover the rich :class:`ImageForensicsReport` carried by a stage result."""
    payload = result.payload
    if not isinstance(payload, ImageForensicsReport):
        raise TypeError(
            f"stage result payload is {type(payload).__name__!r}, "
            f"expected ImageForensicsReport"
        )
    return payload


__all__ = [
    "STAGE_NAME",
    "ImageForensicsStage",
    "report_to_stage_result",
    "stage_result_to_report",
]
