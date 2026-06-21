"""Heatmap → bbox localization (Stage 6, §6) — the pixel-grid → page-point map.

This module turns a method's per-pixel ``ForensicMap.heatmap`` into bounding boxes
in the SAME coordinate convention ``revision_recovery`` and ``ocr_crosscheck``
emit (pdfplumber top-left **points**), so ``aggregate._finding_bbox`` consumes a
Stage 6 region with no UI change. It does **no** signal processing — the heatmap
math lives in the engine; here we only threshold, find connected blobs, and map
their boxes through the per-image placement rectangle from :mod:`.images`.

Coordinate model (§6):

* A heatmap is in the decoded image's pixel grid (a method may down/upscale it,
  so we work in **fractional** grid coordinates ``(u, v) ∈ [0, 1]`` — ``u`` left→
  right, ``v`` top→down — which is resolution-independent).
* ``DecodedImage.placement`` is the image's page rectangle already in pdfplumber
  top-left points ``(x0, top0, x1, top1)``. For an upright placement (the common
  ``w 0 0 h x y cm`` case) image row 0 is the top edge, so a fractional blob maps
  **linearly** into that rectangle. Rotated / flipped placements would need the
  full CTM (not retained); the linear map is the conservative axis-aligned
  approximation and is documented as such.

PHI: a heatmap is document pixels = PHI. Nothing here logs the array — only blob
positions / areas / counts leave this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import ImageForensicsConfig
from .images import DecodedImage

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

#: A page-point bbox in pdfplumber top-left convention: ``(x0, top, x1, bottom)``.
PagePointBBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Blob:
    """One thresholded connected component, in fractional heatmap coordinates.

    ``(u0, v0)`` is the top-left and ``(u1, v1)`` the bottom-right corner, each in
    ``[0, 1]`` (``v`` measured top-down). ``area_frac`` is the component's pixel
    area over the whole heatmap; ``peak`` is the max heatmap value inside it.
    """

    u0: float
    v0: float
    u1: float
    v1: float
    area_frac: float
    peak: float


# --------------------------------------------------------------------------- #
# Heatmap thresholding + connected components
# --------------------------------------------------------------------------- #

def hot_fraction(heatmap: "np.ndarray", threshold: float) -> float:
    """Fraction of the heatmap at or above ``threshold`` (the global-signal test).

    A value near 1.0 means the whole image lit up — whole-image recompression or a
    uniform scanner artifact, not a local edit (the §7 LOW rule). Returns ``0.0``
    for an empty / unusable heatmap; never raises.
    """
    try:
        import numpy as np

        arr = np.asarray(heatmap, dtype=np.float64)
        if arr.size == 0:
            return 0.0
        return float((arr >= threshold).mean())
    except Exception:
        return 0.0


def heatmap_blobs(
    heatmap: "np.ndarray",
    *,
    threshold: float,
    min_area_frac: float,
) -> list[Blob]:
    """Threshold ``heatmap`` and return connected components above ``min_area_frac``.

    Connected components via ``cv2.connectedComponentsWithStats`` (8-connectivity,
    which already merges touching pixels); falls back to ``scipy.ndimage.label``
    and finally to a single bbox of all hot pixels if neither is importable.
    Speckle smaller than ``min_area_frac`` of the image is dropped. Never raises:
    an unusable heatmap yields ``[]``.
    """
    try:
        import numpy as np

        arr = np.asarray(heatmap, dtype=np.float64)
        if arr.ndim != 2 or arr.size == 0:
            return []
        mask = (arr >= threshold).astype(np.uint8)
        if not mask.any():
            return []
        h, w = arr.shape
        area_total = float(h * w)
        comps = _components(mask)
        blobs: list[Blob] = []
        for ys, xs in comps:
            area = float(len(ys))
            if area / area_total < min_area_frac:
                continue
            y0, y1 = int(ys.min()), int(ys.max()) + 1
            x0, x1 = int(xs.min()), int(xs.max()) + 1
            peak = float(arr[ys, xs].max())
            blobs.append(
                Blob(
                    u0=x0 / w,
                    v0=y0 / h,
                    u1=x1 / w,
                    v1=y1 / h,
                    area_frac=area / area_total,
                    peak=peak,
                )
            )
        return blobs
    except Exception:
        return []


def _components(mask: "np.ndarray"):
    """Yield ``(rows, cols)`` index arrays per connected component of ``mask``."""
    import numpy as np

    # Preferred: OpenCV (fast, 8-connectivity).
    try:
        import cv2

        n, labels, _stats, _c = cv2.connectedComponentsWithStats(mask, connectivity=8)
        return [np.where(labels == i) for i in range(1, n)]
    except Exception:
        pass
    # Fallback: scipy.
    try:
        from scipy import ndimage

        labels, n = ndimage.label(mask)
        return [np.where(labels == i) for i in range(1, n + 1)]
    except Exception:
        pass
    # Last resort: one bbox over every hot pixel (no separation).
    ys, xs = np.where(mask > 0)
    return [(ys, xs)] if ys.size else []


# --------------------------------------------------------------------------- #
# Pixel grid → page points
# --------------------------------------------------------------------------- #

def blob_to_page_bbox(blob: Blob, image: DecodedImage) -> PagePointBBox | None:
    """Map a fractional ``blob`` into ``image``'s page-point placement rectangle.

    Returns a pdfplumber top-left ``(x0, top, x1, bottom)`` box in points, or
    ``None`` when the image has no known placement (never a wrong box — §2).
    """
    return frac_bbox_to_page_bbox((blob.u0, blob.v0, blob.u1, blob.v1), image)


def frac_bbox_to_page_bbox(
    frac_bbox: tuple[float, float, float, float], image: DecodedImage
) -> PagePointBBox | None:
    """Linear map of a fractional ``(u0, v0, u1, v1)`` box into the placement rect.

    ``u`` runs left→right and ``v`` top→down, matching the placement rect's
    ``(x0, top0, x1, top1)`` (top-left points). Hand-checked in the tests against
    known corners.
    """
    if image.placement is None:
        return None
    u0, v0, u1, v1 = frac_bbox
    px0, ptop0, px1, ptop1 = image.placement
    x_a = px0 + u0 * (px1 - px0)
    x_b = px0 + u1 * (px1 - px0)
    t_a = ptop0 + v0 * (ptop1 - ptop0)
    t_b = ptop0 + v1 * (ptop1 - ptop0)
    return (min(x_a, x_b), min(t_a, t_b), max(x_a, x_b), max(t_a, t_b))


# --------------------------------------------------------------------------- #
# Geometry helpers (corroboration + high-value tagging)
# --------------------------------------------------------------------------- #

def iou(a: PagePointBBox, b: PagePointBBox) -> float:
    """Intersection-over-union of two page-point bboxes; ``0.0`` if disjoint."""
    ax0, at0, ax1, at1 = a
    bx0, bt0, bx1, bt1 = b
    ix0, it0 = max(ax0, bx0), max(at0, bt0)
    ix1, it1 = min(ax1, bx1), min(at1, bt1)
    iw, ih = ix1 - ix0, it1 - it0
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    area_a = max(0.0, ax1 - ax0) * max(0.0, at1 - at0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, bt1 - bt0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def union_bbox(a: PagePointBBox, b: PagePointBBox) -> PagePointBBox:
    """Axis-aligned union of two page-point bboxes."""
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def overlaps_high_value(
    page_bbox: PagePointBBox,
    page_height_pt: float | None,
    config: ImageForensicsConfig | None = None,
) -> bool:
    """Whether ``page_bbox`` overlaps the configured high-value (amount) band.

    Conservative POSITIONAL heuristic only (§4): scanned pages have no reliable
    text layer, so we tag by vertical position, never a fabricated token class.
    Returns ``False`` when the page height is unknown (cannot place the band).
    """
    cfg = config or ImageForensicsConfig()
    if page_height_pt is None or page_height_pt <= 0:
        return False
    _x0, top, _x1, bottom = page_bbox
    v0 = top / page_height_pt
    v1 = bottom / page_height_pt
    return v1 >= cfg.high_value_band_top_frac and v0 <= cfg.high_value_band_bottom_frac


__all__ = [
    "PagePointBBox",
    "Blob",
    "hot_fraction",
    "heatmap_blobs",
    "blob_to_page_bbox",
    "frac_bbox_to_page_bbox",
    "iou",
    "union_bbox",
    "overlaps_high_value",
]
