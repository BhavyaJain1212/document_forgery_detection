"""Coordinate alignment + word matching (CPU queue).

Implements the transform from ``docs/STAGE3_DESIGN.md`` §1:
  - ``embedded_to_pixel`` converts pdfminer bbox (PDF user-space, bottom-left
    origin, points) to pixel space (top-left origin) at a given DPI with optional
    /Rotate support.
  - ``quad_to_bbox`` reduces a PaddleOCR quadrilateral to an axis-aligned bbox.
  - ``extract_embedded_words`` turns pdfminer page layouts into pixel-space
    :class:`WordBox` objects via the shared ``core.glyphs`` extractor.
  - ``match_words`` does detection-driven one-to-many matching: center-containment
    first, IoU ≥ ``iou_floor`` fallback.

All matching happens in pixel space; embedded boxes are transformed *into* pixel
space rather than transforming OCR boxes back, because OCR boxes can be rotated
quadrilaterals that are first reduced to axis-aligned bboxes.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from pdfminer.layout import LTPage

from .config import OCRCrossCheckConfig
from .models import WordBox, WordSource


# ---------------------------------------------------------------------------
# Quad → axis-aligned bbox
# ---------------------------------------------------------------------------

def quad_to_bbox(
    quad: Sequence[Sequence[float]],
) -> tuple[float, float, float, float]:
    """Reduce a quadrilateral (4 points) to its axis-aligned bounding box.

    PaddleOCR returns quadrilateral detection boxes, which may be rotated.
    Reducing to ``(left, top, right, bottom)`` in pixel space (top-left origin)
    lets us compare directly with the transformed embedded bboxes.

    ``quad`` is any sequence of 4 points ``[x, y]`` in pixel space.
    """
    xs = [float(p[0]) for p in quad]
    ys = [float(p[1]) for p in quad]
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# Coordinate transform: PDF user-space → pixel space
# ---------------------------------------------------------------------------

def embedded_to_pixel(
    bbox: tuple[float, float, float, float],
    *,
    page_height_pt: float,
    page_width_pt: float = 0.0,
    dpi: int,
    rotate: int = 0,
) -> tuple[float, float, float, float]:
    """Transform a pdfminer bbox (bottom-left, points) → pixel bbox (top-left).

    Implements the design transform (``docs/STAGE3_DESIGN.md`` §1) with full
    support for page ``/Rotate`` (0, 90, 180, 270 degrees CCW).

    The formulas below were verified by tracing all four page corners:

    R=0   → left=x0·s, top=(H-y1)·s, right=x1·s, bottom=(H-y0)·s
              rendered dims: W·s × H·s

    R=90  → left=(H-y1)·s, top=(W-x1)·s, right=(H-y0)·s, bottom=(W-x0)·s
              rendered dims: H·s × W·s  (pypdfium2 swaps width/height)

    R=180 → left=(W-x1)·s, top=(H-y1)·s, right=(W-x0)·s, bottom=(H-y0)·s
              rendered dims: W·s × H·s

    R=270 → left=(H-y1)·s, top=x0·s, right=(H-y0)·s, bottom=x1·s
              rendered dims: H·s × W·s  (pypdfium2 swaps width/height)

    where ``s = dpi/72``, ``W = page_width_pt``, ``H = page_height_pt``.

    ``page_width_pt`` is only needed when ``rotate`` is non-zero; it defaults to
    0.0 and is ignored for ``rotate=0``.

    Returns ``(left, top, right, bottom)`` with ``left < right`` and
    ``top < bottom`` (top-left pixel origin).
    """
    if rotate not in (0, 90, 180, 270):
        raise ValueError(f"rotate must be 0, 90, 180, or 270; got {rotate}")

    scale = dpi / 72.0
    x0, y0, x1, y1 = bbox
    H, W = page_height_pt, page_width_pt

    if rotate == 0:
        return (x0 * scale, (H - y1) * scale, x1 * scale, (H - y0) * scale)
    if rotate == 90:
        return ((H - y1) * scale, (W - x1) * scale, (H - y0) * scale, (W - x0) * scale)
    if rotate == 180:
        return ((W - x1) * scale, (H - y1) * scale, (W - x0) * scale, (H - y0) * scale)
    # rotate == 270
    return ((H - y1) * scale, x0 * scale, (H - y0) * scale, x1 * scale)


# ---------------------------------------------------------------------------
# Matching primitives
# ---------------------------------------------------------------------------

def center_inside(
    inner: tuple[float, float, float, float],
    outer: tuple[float, float, float, float],
) -> bool:
    """True when the center point of ``inner`` lies inside ``outer`` (inclusive).

    Primary matching primitive (§1): an embedded word is claimed by an OCR box
    when its center falls inside that box. Robust to the OCR detector drawing a
    looser or tighter box than the actual glyph extent.

    Both bboxes are ``(left, top, right, bottom)`` in pixel space (top-left
    origin), so ``top < bottom``.
    """
    cx = (inner[0] + inner[2]) / 2.0
    cy = (inner[1] + inner[3]) / 2.0
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def iou(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """Axis-aligned intersection-over-union of two pixel bboxes.

    Fallback matching primitive (§1): used for embedded words whose center does
    not fall inside any OCR box. An embedded↔OCR pair with
    ``IoU ≥ config.iou_floor`` is matched.

    Both bboxes are ``(left, top, right, bottom)`` in pixel space. Returns a
    value in ``[0.0, 1.0]``; returns 0.0 when the boxes do not overlap.
    """
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


# ---------------------------------------------------------------------------
# Embedded word extraction
# ---------------------------------------------------------------------------

def extract_embedded_words(
    page_layouts: list["LTPage"],
    dpi: int,
    page_rotations: list[int] | None = None,
) -> list[WordBox]:
    """Extract pixel-space :class:`WordBox` objects from pdfminer page layouts.

    Uses the shared ``core.glyphs`` extractor (``glyphs_from_layouts`` +
    ``group_lines``) — do NOT add a second extraction path. Each
    :class:`~pdf_forgery.core.glyphs.Token` becomes one :class:`WordBox` with its
    bbox transformed to pixel space via :func:`embedded_to_pixel`.

    ``page_rotations`` is a per-page list of ``/Rotate`` values (0/90/180/270);
    ``None`` or a short list means 0 for unlisted pages.

    PHI note: this function returns the raw text — callers that log must use the
    safe-log helper and never emit ``WordBox.text`` directly.
    """
    from ..core.glyphs import glyphs_from_layouts, group_lines

    glyphs = glyphs_from_layouts(page_layouts)
    lines = group_lines(glyphs)

    rotations: list[int] = page_rotations or []

    words: list[WordBox] = []
    for line in lines:
        page_idx = line.page_index
        page = page_layouts[page_idx]
        page_h = float(page.height)
        page_w = float(page.width)
        rotate = rotations[page_idx] if page_idx < len(rotations) else 0

        for token in line.tokens:
            if not token.text.strip():
                continue
            px_bbox = embedded_to_pixel(
                token.bbox,
                page_height_pt=page_h,
                page_width_pt=page_w,
                dpi=dpi,
                rotate=rotate,
            )
            words.append(
                WordBox(
                    text=token.text,
                    bbox=px_bbox,
                    source=WordSource.EMBEDDED,
                    conf=None,
                    page_index=page_idx,
                )
            )
    return words


# ---------------------------------------------------------------------------
# Word matching — one-to-many, detection-driven (§1)
# ---------------------------------------------------------------------------

MatchGroup = tuple[tuple[WordBox, ...], WordBox]
"""One OCR box paired with the ≥1 embedded words it claims."""


def match_words(
    embedded: list[WordBox],
    ocr: list[WordBox],
    config: OCRCrossCheckConfig | None = None,
) -> tuple[list[MatchGroup], list[WordBox], list[WordBox]]:
    """Match embedded ↔ OCR words for ONE page (one-to-many, §1).

    Returns ``(groups, unmatched_embedded, unmatched_ocr)`` where each group is
    ``(embedded_words_tuple, ocr_box)``; embedded words in a group are sorted in
    reading order (top-to-bottom, left-to-right by center point) for downstream
    re-joining.

    Two-pass matching:
    1. Center-containment (primary, ``config.use_center_containment``): an
       embedded word is claimed by the OCR box whose rect contains the embedded
       word's center point. One OCR box may claim many embedded words.
    2. IoU fallback: unmatched embedded words with ``IoU ≥ config.iou_floor``
       against any unclaimed OCR box get matched 1:1 (best-IoU wins). An OCR box
       already claimed by center-containment is NOT eligible for the IoU fallback
       (it would double-count).

    Unmatched embedded words after both passes → caller treats as
    ``EMBEDDED_ONLY`` candidates (subject to clipping guard).
    OCR boxes with no embedded words → caller treats as ``OCR_ONLY`` candidates
    (subject to confidence floor — already applied upstream in ``guards``).
    """
    cfg = config or OCRCrossCheckConfig()

    if not embedded or not ocr:
        return [], list(embedded), list(ocr)

    # Map each OCR box index → list of embedded words inside it (center-containment)
    ocr_to_embedded: dict[int, list[WordBox]] = {i: [] for i in range(len(ocr))}
    unmatched_emb: list[WordBox] = []

    if cfg.use_center_containment:
        for e in embedded:
            matched = False
            for oi, ob in enumerate(ocr):
                if center_inside(e.bbox, ob.bbox):
                    ocr_to_embedded[oi].append(e)
                    matched = True
                    break  # first containing OCR box wins
            if not matched:
                unmatched_emb.append(e)
    else:
        unmatched_emb = list(embedded)

    # IoU fallback for unmatched embedded words.
    # Only OCR boxes that have NO center-containment matches are eligible.
    unclaimed_ocr_idx = {oi for oi, ew in ocr_to_embedded.items() if not ew}
    still_unmatched: list[WordBox] = []

    for e in unmatched_emb:
        best_oi: int | None = None
        best_iou = 0.0
        for oi in unclaimed_ocr_idx:
            v = iou(e.bbox, ocr[oi].bbox)
            if v >= cfg.iou_floor and v > best_iou:
                best_iou = v
                best_oi = oi
        if best_oi is not None:
            ocr_to_embedded[best_oi].append(e)
            unclaimed_ocr_idx.discard(best_oi)  # claimed — remove from fallback pool
        else:
            still_unmatched.append(e)

    # Build groups, sorting embedded words within each group in reading order.
    groups: list[MatchGroup] = []
    for oi, ew in ocr_to_embedded.items():
        if not ew:
            continue
        sorted_ew = sorted(ew, key=lambda w: (_cy(w.bbox), _cx(w.bbox)))
        groups.append((tuple(sorted_ew), ocr[oi]))

    unmatched_ocr = [ocr[oi] for oi in range(len(ocr)) if not ocr_to_embedded[oi]]
    return groups, still_unmatched, unmatched_ocr


def _cx(bbox: tuple[float, float, float, float]) -> float:
    return (bbox[0] + bbox[2]) / 2.0


def _cy(bbox: tuple[float, float, float, float]) -> float:
    return (bbox[1] + bbox[3]) / 2.0


__all__ = [
    "quad_to_bbox",
    "embedded_to_pixel",
    "center_inside",
    "iou",
    "extract_embedded_words",
    "match_words",
]
