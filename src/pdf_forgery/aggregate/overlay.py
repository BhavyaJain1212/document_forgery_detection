"""Bake an annotated PNG over a rendered page (PHI-side gated evidence).

Draws each located bounding box (pdfplumber points, top-left origin) onto a
``pypdfium2`` raster of the page using Pillow. Both libraries render in the page's
visual (rotation-applied) orientation, so the only conversion is the scale
``pixel = point * dpi/72``.

**This image contains real document pixels — it is PHI.** It must only be served
via the gated evidence endpoint, NEVER through the scrubbed ``AdvisoryInput`` /
advisory channel (only the normalized :class:`~pdf_forgery.aggregate.models.BBox`
coordinates cross the PHI boundary).

Returns ``None`` (never raises) when ``pypdfium2`` / Pillow are unavailable, the
page index is out of range, or rendering fails — callers degrade gracefully.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from io import BytesIO

from .config import AggregateConfig

# pypdfium2 is NOT thread-safe: concurrent ``PdfDocument`` use shares global
# native state and corrupts renders (under the reviewer UI a 13-page document
# fires ~6+ parallel page-image requests, and most came back None/blank). Every
# render here goes through this single process-wide lock so the burst is
# serialized. Renders are sub-0.1s, so serializing them is cheap.
_RENDER_LOCK = threading.Lock()


def render_page_overlay(
    pdf_bytes: bytes,
    page_index: int,
    boxes_pt: Iterable[tuple[float, float, float, float]],
    *,
    config: AggregateConfig | None = None,
) -> bytes | None:
    """Return PNG bytes of page ``page_index`` with ``boxes_pt`` highlighted.

    ``boxes_pt`` are ``(x0, top, x1, bottom)`` rectangles in pdfplumber page space
    (PDF points, top-left origin). ``None`` on any failure.
    """
    cfg = config or AggregateConfig()
    try:
        import pypdfium2 as pdfium
        from PIL import Image, ImageDraw
    except Exception:
        return None

    boxes = list(boxes_pt)
    try:
        scale = cfg.overlay_dpi / 72.0
        # Serialize the pypdfium2 render (not thread-safe — see _RENDER_LOCK).
        with _RENDER_LOCK:
            doc = pdfium.PdfDocument(pdf_bytes)
            try:
                if page_index < 0 or page_index >= len(doc):
                    return None
                bitmap = doc[page_index].render(scale=scale)
                base = bitmap.to_pil().convert("RGBA")
            finally:
                doc.close()

        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        r, g, b = cfg.overlay_box_rgb
        fill_alpha = int(max(0.0, min(1.0, cfg.overlay_fill_alpha)) * 255)
        for x0, top, x1, bottom in boxes:
            rect = (x0 * scale, top * scale, x1 * scale, bottom * scale)
            draw.rectangle(
                rect,
                outline=(r, g, b, 255),
                width=cfg.overlay_box_width,
                fill=(r, g, b, fill_alpha),
            )

        composited = Image.alpha_composite(base, overlay).convert("RGB")
        buf = BytesIO()
        composited.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


__all__ = ["render_page_overlay"]
