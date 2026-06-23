"""Roll the per-stage results up into one :class:`AggregateResult`.

The headline is computed by delegating to the existing
:func:`pdf_forgery.fusion.fuse` (Stage 7 adds no new fusion math); this module
then flattens findings into descriptors, assigns stable ``finding_id``\\s, and
attaches the canonical-space ``bbox`` for each so the future overlay is a pure
render job.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..core.geometry import pdf_bbox_to_canonical, pixel_bbox_to_canonical
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

# image_forensics: stable advisory-safe type token for each classical method.
# Method names come from the detector implementation, never from document data.
_IMAGE_METHOD_TYPE = {
    "ela": "image_ela",
    "double_jpeg": "image_double_jpeg",
    "jpeg_grid": "image_jpeg_grid",
    "noise_inconsistency": "image_noise",
    "copy_move": "image_copy_move",
}


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

    if stage == "image_forensics":
        rich = _payload_findings(payload)
        if rich is not None and index < len(rich):
            region = getattr(rich[index], "region", None)
            # The stage adapter maps RegionFinding objects 1:1 into core
            # Findings. Verify the page before trusting that positional link.
            if region is not None and finding.page == getattr(
                region, "page_index", None
            ):
                if getattr(region, "co_located", False):
                    return "image_splice"
                methods = getattr(region, "methods", ())
                if methods:
                    return _IMAGE_METHOD_TYPE.get(methods[0], "image_anomaly")
        return "image_anomaly"

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


def _clamp01(value: float) -> float:
    """Clamp ``value`` into ``[0, 1]`` (defensive against off-page coordinates)."""
    return max(0.0, min(1.0, value))


def _finding_bbox(stage_result: StageResult, finding: Finding, index: int) -> BBox | None:
    """Convert a finding's native geometry into the canonical normalized box.

    Each stage stores its bbox in a different native coordinate space.
    This function normalizes all of them into canonical ``[0,1]`` top-left
    :class:`BBox` using per-stage branches.  Returns ``None`` when localization
    is not possible (missing page dims, sentinel bbox, guard mismatch).

    Positional-integrity guards verify that the rich finding at ``payload[index]``
    actually corresponds to ``finding`` (matching page + natural identifier) before
    trusting the index.  On any mismatch the function returns ``None`` rather than
    attaching a box to the wrong finding.
    """
    stage = stage_result.stage
    payload = stage_result.payload

    # ------------------------------------------------------------------ #
    # revision_recovery — pdfplumber top-left points, no origin flip needed
    # ------------------------------------------------------------------ #
    if stage == "revision_recovery":
        rich = _payload_findings(payload)
        if rich is None or index >= len(rich):
            return None
        rf = rich[index]
        if finding.page != getattr(rf, "page_index", None):
            return None
        if tuple(finding.object_ids) != tuple(getattr(rf, "object_ids", ())):
            return None
        location = getattr(rf, "location", None)
        if location is None or not location.boxes:
            return None
        width = location.page_width_pt
        height = location.page_height_pt
        if width <= 0 or height <= 0:
            return None
        x0 = min(b.x0 for b in location.boxes)
        y0 = min(b.top for b in location.boxes)
        x1 = max(b.x1 for b in location.boxes)
        y1 = max(b.bottom for b in location.boxes)
        return BBox(
            x0=_clamp01(x0 / width),
            y0=_clamp01(y0 / height),
            x1=_clamp01(x1 / width),
            y1=_clamp01(y1 / height),
        )

    # ------------------------------------------------------------------ #
    # invoice_arithmetic — PDF user space (bottom-left, points)
    # ------------------------------------------------------------------ #
    if stage == "invoice_arithmetic":
        rich = _payload_findings(payload)
        if rich is None or index >= len(rich):
            return None
        rf = rich[index]
        # Positional-integrity guard: page + high_value.
        if finding.page != getattr(rf, "page_index", None):
            return None
        rf_hv = getattr(rf, "high_value", None)
        rf_hv_str = rf_hv.value if rf_hv is not None else None
        if finding.high_value != rf_hv_str:
            return None
        bbox = getattr(rf, "bbox", (0.0, 0.0, 0.0, 0.0))
        if bbox == (0.0, 0.0, 0.0, 0.0):
            return None  # output cell was None (sentinel)
        page = getattr(rf, "page_index", None)
        dims = getattr(payload, "page_dims", ())
        if page is None or page >= len(dims):
            return None
        W, H = dims[page]
        rots = getattr(payload, "page_rotations", ())
        rot = rots[page] if page < len(rots) else 0
        result = pdf_bbox_to_canonical(bbox, page_width_pt=W, page_height_pt=H, rotate=rot)
        if result is None:
            return None
        return BBox(*result)

    # ------------------------------------------------------------------ #
    # font_forensics — PDF user space (bottom-left, points)
    # ------------------------------------------------------------------ #
    if stage == "font_forensics":
        rich = _payload_findings(payload)
        if rich is None or index >= len(rich):
            return None
        rf = rich[index]
        # Positional-integrity guard: page + high_value.
        if finding.page != getattr(rf, "page_index", None):
            return None
        rf_hv = getattr(rf, "high_value", None)
        rf_hv_str = rf_hv.value if rf_hv is not None else None
        if finding.high_value != rf_hv_str:
            return None
        page = getattr(rf, "page_index", None)
        dims = getattr(payload, "page_dims", ())
        if page is None or page >= len(dims):
            return None
        W, H = dims[page]
        rots = getattr(payload, "page_rotations", ())
        rot = rots[page] if page < len(rots) else 0
        # For INTRA_TOKEN_FONT_MIX, prefer the union of suspicious glyph bboxes
        # (precise — points at the inserted char); fall back to the token bbox.
        kind_val = getattr(getattr(rf, "kind", None), "value", "")
        suspicious = getattr(rf, "suspicious_bboxes", ())
        if kind_val == "intra_token_font_mix" and suspicious:
            all_boxes = list(suspicious)
        else:
            token_bbox = getattr(rf, "bbox", (0.0, 0.0, 0.0, 0.0))
            if token_bbox == (0.0, 0.0, 0.0, 0.0):
                return None
            all_boxes = [token_bbox]
        ux0 = min(b[0] for b in all_boxes)
        uy0 = min(b[1] for b in all_boxes)
        ux1 = max(b[2] for b in all_boxes)
        uy1 = max(b[3] for b in all_boxes)
        result = pdf_bbox_to_canonical(
            (ux0, uy0, ux1, uy1), page_width_pt=W, page_height_pt=H, rotate=rot
        )
        if result is None:
            return None
        return BBox(*result)

    # ------------------------------------------------------------------ #
    # ocr_crosscheck — pixel space (top-left, already rotation-correct)
    # ------------------------------------------------------------------ #
    if stage == "ocr_crosscheck":
        # Use the AGREE-filtered divergence list (same filter the adapter applied).
        divergences = _ocr_divergences_excluding_agree(payload)
        if divergences is None or index >= len(divergences):
            return None
        d = divergences[index]
        # Positional-integrity guard: page + high_value mapping.
        if finding.page != getattr(d, "page_index", None):
            return None
        tc = getattr(d, "token_class", None)
        tc_val = getattr(tc, "value", None)
        ocr_hv = tc_val if tc_val in ("amount", "date", "id") else None
        if finding.high_value != ocr_hv:
            return None
        page = getattr(d, "page_index", None)
        dims_px = getattr(payload, "page_dims_px", ())
        if page is None or page >= len(dims_px):
            return None
        Wpx, Hpx = dims_px[page]
        boxes: list[tuple[float, float, float, float]] = []
        for w in getattr(d, "embedded", ()):
            boxes.append(w.bbox)
        ocr_box = getattr(d, "ocr", None)
        if ocr_box is not None:
            boxes.append(ocr_box.bbox)
        if not boxes:
            return None
        ux0 = min(b[0] for b in boxes)
        uy0 = min(b[1] for b in boxes)
        ux1 = max(b[2] for b in boxes)
        uy1 = max(b[3] for b in boxes)
        result = pixel_bbox_to_canonical(
            (ux0, uy0, ux1, uy1), page_width_px=Wpx, page_height_px=Hpx
        )
        if result is None:
            return None
        return BBox(*result)

    # ------------------------------------------------------------------ #
    # image_forensics — pdfplumber top-left points (heatmap blob → page),
    # no origin flip needed (mirrors revision_recovery)
    # ------------------------------------------------------------------ #
    if stage == "image_forensics":
        rich = _payload_findings(payload)
        if rich is None or index >= len(rich):
            return None
        region = getattr(rich[index], "region", None)
        if region is None:
            return None
        # Positional-integrity guard: the RegionFinding→Finding map is 1:1, but
        # verify the page before trusting the index (never a wrong box).
        if finding.page != getattr(region, "page_index", None):
            return None
        bbox = getattr(region, "page_bbox", None)
        if bbox is None:
            return None  # no resolved placement (e.g. nested-in-form image)
        width = getattr(region, "page_width_pt", None)
        height = getattr(region, "page_height_pt", None)
        if not width or not height or width <= 0 or height <= 0:
            return None
        x0, top, x1, bottom = bbox
        return BBox(
            x0=_clamp01(x0 / width),
            y0=_clamp01(top / height),
            x1=_clamp01(x1 / width),
            y1=_clamp01(bottom / height),
        )

    return None


__all__ = ["aggregate"]
