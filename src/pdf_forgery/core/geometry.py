"""Geometry helpers: convert per-stage native bboxes to canonical [0,1] top-left form.

Two native coordinate spaces exist across the pipeline stages:

  - **PDF user space** (bottom-left origin, points): ``invoice_arithmetic``,
    ``font_forensics``.  Needs a y-flip and, for rotated pages, a rotation
    transform before normalizing.
  - **Pixel space** (top-left origin, pixels): ``ocr_crosscheck``.  Rotation is
    already baked in by ``align.embedded_to_pixel``; only a divide+clamp is needed.

All functions are pure and dependency-free (no third-party imports).  They return
raw coordinate tuples; callers construct :class:`~pdf_forgery.aggregate.models.BBox`
from the result.

Rotation formulas mirror ``ocr_crosscheck.align.embedded_to_pixel`` at ``dpi=72``
(scale = 1.0, so the dpi factor cancels under normalization).  For rotations 90/270
pypdfium2 swaps the rendered image's width and height.

  R=0:    px=(x0, H-y1, x1, H-y0)            rendered W×H
  R=90:   px=(H-y1, W-x1, H-y0, W-x0)        rendered H×W
  R=180:  px=(W-x1, H-y1, W-x0, H-y0)        rendered W×H
  R=270:  px=(H-y1, x0,   H-y0, x1  )        rendered H×W

where W=page_width_pt, H=page_height_pt.
"""

from __future__ import annotations


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def pdf_bbox_to_canonical(
    bbox: tuple[float, float, float, float],
    *,
    page_width_pt: float,
    page_height_pt: float,
    rotate: int = 0,
) -> tuple[float, float, float, float] | None:
    """PDF user-space bbox (bottom-left origin, points) → normalized [0,1] top-left.

    Returns ``None`` if page dims are non-positive or ``rotate`` not in
    ``{0, 90, 180, 270}``.
    """
    if page_width_pt <= 0 or page_height_pt <= 0:
        return None
    if rotate not in (0, 90, 180, 270):
        return None

    W, H = page_width_pt, page_height_pt
    x0, y0, x1, y1 = bbox

    if rotate == 0:
        rw, rh = W, H
        px0, py0, px1, py1 = x0, H - y1, x1, H - y0
    elif rotate == 90:
        rw, rh = H, W
        px0, py0, px1, py1 = H - y1, W - x1, H - y0, W - x0
    elif rotate == 180:
        rw, rh = W, H
        px0, py0, px1, py1 = W - x1, H - y1, W - x0, H - y0
    else:  # 270
        rw, rh = H, W
        px0, py0, px1, py1 = H - y1, x0, H - y0, x1

    return (
        _clamp01(px0 / rw),
        _clamp01(py0 / rh),
        _clamp01(px1 / rw),
        _clamp01(py1 / rh),
    )


def pixel_bbox_to_canonical(
    bbox: tuple[float, float, float, float],
    *,
    page_width_px: float,
    page_height_px: float,
) -> tuple[float, float, float, float] | None:
    """Top-left pixel bbox → normalized [0,1] canonical form (divide + clamp).

    Returns ``None`` if page dims are non-positive.
    """
    if page_width_px <= 0 or page_height_px <= 0:
        return None
    x0, y0, x1, y1 = bbox
    return (
        _clamp01(x0 / page_width_px),
        _clamp01(y0 / page_height_px),
        _clamp01(x1 / page_width_px),
        _clamp01(y1 / page_height_px),
    )


__all__ = ["pdf_bbox_to_canonical", "pixel_bbox_to_canonical"]
