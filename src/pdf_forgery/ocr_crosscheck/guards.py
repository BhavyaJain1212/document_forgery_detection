"""False-positive guards — run BEFORE any unmatched word counts (CPU queue).

Two guards per ``docs/STAGE3_DESIGN.md`` §4:

(a) **Clipping guard** — embedded words whose pixel bbox is wholly outside the
    rendered page rect are off-page operators that never render.  This is the
    pdfminer-vs-fitz/raster discrepancy already understood from Stage 1: pdfminer
    reports text-positioning operators even when they are positioned outside the
    page's visible box.  OCR correctly never sees them, so naïvely they would
    look like mass EMBEDDED_ONLY divergence.  Guard: words whose **center** falls
    outside ``[0 − margin, page_width + margin] × [0 − margin, page_height +
    margin]`` are excluded from scoring and the denominator entirely.

(b) **OCR-confidence floor** — OCR words below ``ocr_conf_floor`` (default 0.50)
    are the engine guessing at texture/noise.  Dropped before matching.

Both record a PHI-safe count (not the text) as a diagnostic.
"""

from __future__ import annotations

from .config import OCRCrossCheckConfig
from .models import WordBox


def filter_offpage_embedded(
    words: list[WordBox],
    *,
    page_width_px: float,
    page_height_px: float,
    config: OCRCrossCheckConfig | None = None,
) -> tuple[list[WordBox], int]:
    """Clipping guard (§4a): drop embedded words whose center is off-page.

    A word is *off-page* when its center ``(cx, cy)`` falls outside the page
    rect ``[-margin, page_width_px + margin] × [-margin, page_height_px +
    margin]`` where ``margin = config.clip_margin_px`` (default 2 px).

    Words whose center is inside the rect but whose box overruns an edge are
    **kept** — partial overlap with the page boundary is normal for glyphs near
    the crop box.

    Returns ``(kept_words, dropped_count)``.  The count is the only PHI-safe
    diagnostic emitted; the dropped words themselves are discarded silently.
    """
    cfg = config or OCRCrossCheckConfig()
    margin = cfg.clip_margin_px

    kept: list[WordBox] = []
    dropped = 0

    lo_x = -margin
    hi_x = page_width_px + margin
    lo_y = -margin
    hi_y = page_height_px + margin

    for w in words:
        cx = (w.bbox[0] + w.bbox[2]) / 2.0
        cy = (w.bbox[1] + w.bbox[3]) / 2.0
        if lo_x <= cx <= hi_x and lo_y <= cy <= hi_y:
            kept.append(w)
        else:
            dropped += 1

    return kept, dropped


def filter_low_confidence_ocr(
    words: list[WordBox],
    config: OCRCrossCheckConfig | None = None,
) -> tuple[list[WordBox], int]:
    """OCR-confidence floor (§4b): drop OCR words below ``ocr_conf_floor``.

    Words with ``conf is None`` are **kept** (embedded words have no confidence;
    this guard should only receive OCR-sourced words but is safe for mixed input).

    Returns ``(kept_words, dropped_count)``.
    """
    cfg = config or OCRCrossCheckConfig()
    floor = cfg.ocr_conf_floor

    kept: list[WordBox] = []
    dropped = 0

    for w in words:
        if w.conf is not None and w.conf < floor:
            dropped += 1
        else:
            kept.append(w)

    return kept, dropped


__all__ = ["filter_offpage_embedded", "filter_low_confidence_ocr"]
