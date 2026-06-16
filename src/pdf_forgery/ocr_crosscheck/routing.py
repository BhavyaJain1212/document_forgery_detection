"""Routing — scanned / text-sparse short-circuit (CPU queue).

Stage 3 is only meaningful on a digital-native PDF with a real embedded text
layer.  A scanned / image-only PDF has (almost) no embedded text → every OCR
word would be ``OCR_ONLY`` → naïve scoring would report HIGH forgery on an
innocent scan.  That is the WRONG route; image forensics handles scanned PDFs.

Detect the text-sparse case (§5) and decline — emit INCONCLUSIVE with a
hand-off flag rather than mass divergence.  This mirrors ``revision_recovery``'s
single-revision INCONCLUSIVE: the method does not apply, so say so and route on.
"""

from __future__ import annotations

from .config import OCRCrossCheckConfig
from .models import WordBox


def is_text_sparse(
    embedded: list[WordBox],
    ocr: list[WordBox],
    config: OCRCrossCheckConfig | None = None,
) -> bool:
    """True when the document is scanned / text-sparse (§5).

    Fires when EITHER of these conditions holds (checked on total counts across
    all pages, after the §4 guards have already been applied):

    1. ``len(embedded) < config.min_embedded_words`` (absolute floor, default 10)
    2. ``len(embedded) < config.embedded_ocr_ratio_floor * len(ocr)``
       (ratio check: fewer than 1 embedded word per 10 OCR words ⇒ OCR-only)

    Condition 2 is skipped when ``ocr`` is empty (avoids division by zero and
    matches the "no OCR ran" path, which is handled upstream as INCONCLUSIVE for
    a different reason).
    """
    cfg = config or OCRCrossCheckConfig()
    n_emb = len(embedded)
    n_ocr = len(ocr)

    if n_emb < cfg.min_embedded_words:
        return True
    if n_ocr > 0 and n_emb < cfg.embedded_ocr_ratio_floor * n_ocr:
        return True
    return False


__all__ = ["is_text_sparse"]
