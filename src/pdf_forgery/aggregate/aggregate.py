"""Roll the per-stage results up into one :class:`AggregateResult`.

The headline is computed by delegating to the existing
:func:`pdf_forgery.fusion.fuse` (Stage 6 adds no new fusion math); this module
then flattens findings into descriptors, assigns stable ``finding_id``\\s, and
attaches the canonical-space ``bbox`` for each so the future overlay is a pure
render job.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..core.types import Finding, StageResult
from ..fusion import fuse
from .config import AggregateConfig
from .models import AggregateFinding, AggregateResult, BBox

#: High-value classes that pass straight through as ``token_class``.
_HIGH_VALUE_TOKEN_CLASSES = frozenset({"amount", "date", "id"})

# revision_recovery: priority order when a finding spans multiple
# ObjectChangeClass tags (most severe first) -> stable type token.
_OBJECT_CLASS_TYPE = (
    ("CONTENT", "content_edit"),
    ("OVERLAY", "overlay"),
    ("FIELD_EDIT", "field_edit"),
    ("FORM_FILL", "form_fill"),
    ("SIGNATURE", "signature_change"),
    ("MARKUP", "markup"),
    ("META", "metadata_change"),
)


def aggregate(
    results: Sequence[StageResult],
    config: AggregateConfig | None = None,
) -> AggregateResult:
    """Combine stage results into one :class:`AggregateResult` (never raises).

    Delegates the headline tier/score/reasons to :func:`pdf_forgery.fusion.fuse`
    (via ``config.fusion``), then flattens every stage's findings into
    :class:`AggregateFinding` descriptors with stable ids and canonical ``bbox``.
    """
    cfg = config or AggregateConfig()
    fused = fuse(results, cfg.fusion)
    findings = _flatten_findings(results)

    return AggregateResult(
        tier=fused.tier,
        score=fused.score,
        stage_results=tuple(results),
        findings=findings,
        reasons=fused.reasons,
        contributing_stages=fused.contributing_stages,
        notes=fused.notes,
    )


def _flatten_findings(results: Sequence[StageResult]) -> tuple[AggregateFinding, ...]:
    """Flatten every stage's findings into descriptors with stable ids.

    Assigns ``finding_id = f"{stage}-{n}"`` (n = per-stage index), derives
    ``type`` / ``token_class`` / ``bbox`` per finding, and drops raw text.
    """
    flat: list[AggregateFinding] = []
    for result in results:
        for index, finding in enumerate(result.findings):
            flat.append(
                AggregateFinding(
                    finding_id=f"{result.stage}-{index}",
                    stage=result.stage,
                    type=_finding_type(result, finding, index),
                    tier=finding.tier,
                    # Finding (core) carries no per-finding numeric score, only
                    # a tier; the only score that exists is the stage-level one
                    # on AggregateResult/StageResult, not per finding.
                    score=None,
                    token_class=_token_class(finding),
                    page=finding.page,
                    bbox=_finding_bbox(result, finding, index),
                )
            )
    return tuple(flat)


def _token_class(finding: Finding) -> str | None:
    """``"amount"``/``"date"``/``"id"`` from ``Finding.high_value``, else
    ``"prose"`` when there is a text change with no high-value tag, else
    ``None`` when the finding carries no text evidence at all."""
    if finding.high_value in _HIGH_VALUE_TOKEN_CLASSES:
        return finding.high_value
    if finding.before or finding.after:
        return "prose"
    return None


def _finding_type(stage_result: StageResult, finding: Finding, index: int) -> str:
    """Derive the finding/forgery-method ``type`` token for one finding.

    Should converge on the canonical literals in ``docs/FORGERY_METHODS.md``;
    until each adapter exposes one, this derives it from the stage's rich
    ``payload`` (positionally correlated to ``stage_result.findings`` — every
    adapter except ``ocr_crosscheck`` builds its core findings via an
    unfiltered 1:1 map, and ``ocr_crosscheck`` is corrected for below by
    skipping AGREE divergences the same way its adapter does).
    """
    payload = stage_result.payload
    stage = stage_result.stage

    if stage == "ocr_crosscheck":
        divergences = _ocr_divergences_excluding_agree(payload)
        if divergences is not None and index < len(divergences):
            return divergences[index].type.value
        return finding.high_value or "divergence"

    if stage == "revision_recovery":
        rich = _payload_findings(payload)
        if rich is not None and index < len(rich):
            classes = {c.name for c in rich[index].object_classes}
            for member_name, type_token in _OBJECT_CLASS_TYPE:
                if member_name in classes:
                    return type_token
        return finding.high_value or "content_edit"

    if stage == "font_forensics":
        rich = _payload_findings(payload)
        if rich is not None and index < len(rich):
            return rich[index].kind.value
        return "font_inconsistency"

    if stage == "invoice_arithmetic":
        rich = _payload_findings(payload)
        if rich is not None and index < len(rich):
            return rich[index].relationship_kind.value
        return "broken_relationship"

    if stage == "provenance_metadata":
        rich = _payload_findings(payload)
        if rich is not None and index < len(rich):
            return rich[index].kind.value
        return "provenance_anomaly"

    return finding.high_value or "finding"


def _payload_findings(payload: object) -> Sequence | None:
    """Return ``payload.findings`` (the rich per-stage finding list) when
    present, else ``None``. Every stage report exposes ``.findings`` except the
    not-ok / no-result cases, which already produce an empty core findings list
    so this is never indexed into in that case."""
    findings = getattr(payload, "findings", None)
    return findings


def _ocr_divergences_excluding_agree(payload: object):
    """Recover ``payload.result.divergences`` filtered the same way the
    ocr_crosscheck adapter filters them when building core ``Finding``\\s
    (AGREE divergences are not findings), so the index lines up."""
    result = getattr(payload, "result", None)
    if result is None:
        return None
    divergences = getattr(result, "divergences", None)
    if divergences is None:
        return None
    return [d for d in divergences if d.type.value != "agree"]


def _finding_bbox(stage_result: StageResult, finding: Finding, index: int) -> BBox | None:
    """Convert a finding's native geometry into the canonical normalized box.

    Native spaces differ per stage (``ocr_crosscheck``: pixel space at
    ``render_dpi``; ``font_forensics`` / ``invoice_arithmetic``: PDF points,
    bottom-left origin). Returns ``None`` for every stage this slice: the
    geometry exists on each stage's rich payload, but normalizing it to
    ``[0, 1]`` needs the page's pixel/point dimensions, which none of the
    stage payloads currently carry (``ocr_crosscheck``'s ``RenderProvenance``
    has ``render_dpi`` but no per-page size; ``font_forensics`` /
    ``invoice_arithmetic`` have the PDF-points bbox but no page height to flip
    the bottom-left origin). Re-deriving it would mean re-opening the source
    PDF, which this function does not have access to. The contract is fixed
    now; wiring real bboxes is a follow-up once a stage payload carries page
    dimensions.
    """
    return None


__all__ = ["aggregate"]
