"""Glyph-based FALLBACK text extraction for revision comparison.

The primary text extractor (:mod:`.text`) collects pdfminer ``LTTextContainer``
text at the page's top level. On some producers (notably files edited in
Acrobat) that path returns only a fragment of the page — a single text container
— while the changed amount lives in glyphs nested deeper in the layout tree. The
revision diff then sees no change and stalls at MEDIUM.

This module reuses the SHARED per-character glyph extractor
(:mod:`pdf_forgery.core.glyphs`) — the very one ``font_forensics`` relies on — to
reconstruct readable per-page text from grouped lines/tokens, preserving page
indexes and exact token text. It is used only as a FALLBACK when the primary
extraction looks suspiciously incomplete (see :func:`looks_incomplete`), so files
where primary extraction already works are never affected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.glyphs import glyphs_from_bytes, group_lines
from ..highvalue import classify_token
from .normalize import normalize, tokenize

if TYPE_CHECKING:
    from ..config import Config


def glyph_page_texts(data: bytes) -> list[str]:
    """Reconstruct readable text per page from per-character glyphs.

    Groups glyphs into lines (top-to-bottom) and tokens (left-to-right), then
    joins tokens with single spaces and lines with newlines, one string per page
    in page order. Tolerant: malformed input yields ``[]``.
    """
    glyphs = glyphs_from_bytes(data)
    if not glyphs:
        return []
    lines = group_lines(glyphs)

    max_page = max(g.page_index for g in glyphs)
    by_page: dict[int, list[str]] = {i: [] for i in range(max_page + 1)}
    for line in lines:
        text = " ".join(t.text for t in line.tokens if t.text)
        if text:
            by_page[line.page_index].append(text)
    return ["\n".join(by_page[i]) for i in range(max_page + 1)]


def _nonspace_len(pages: list[str]) -> int:
    return sum(len("".join(p.split())) for p in pages)


def _high_value_tokens(pages: list[str], config: "Config | None") -> set[str]:
    """Set of normalised high-value tokens (amount/date/ID) present in *pages*."""
    found: set[str] = set()
    for page in pages:
        for tok in tokenize(normalize(page, config)):
            if classify_token(tok) is not None:
                found.add(tok)
    return found


def looks_incomplete(
    primary_pages_normalized: list[str],
    data: bytes,
    config: "Config | None" = None,
) -> bool:
    """Heuristic: did primary extraction miss a large share of the real text?

    Returns True when the glyph path recovers substantially more text than the
    primary extractor did (a low primary/glyph character ratio), OR when the
    glyph path surfaces high-value tokens (amounts/dates/IDs) that the primary
    text does not contain. Both signal that the primary text is only a fragment
    and a glyph-based comparison is warranted.

    Conservative by design: when the glyph path yields little text, or primary
    already captured a comparable amount, it returns False so working files are
    untouched.
    """
    from ..config import Config

    cfg = config or Config()

    glyph_pages = glyph_page_texts(data)
    glyph_chars = _nonspace_len(glyph_pages)
    if glyph_chars < cfg.fallback_min_glyph_chars:
        return False  # nothing meaningful to recover via glyphs

    primary_chars = _nonspace_len(primary_pages_normalized)
    if primary_chars < cfg.fallback_incomplete_ratio * glyph_chars:
        return True

    # Same character volume but the glyph path sees high-value tokens the
    # primary text is missing -> the amount/date the diff cares about was dropped.
    primary_hv = _high_value_tokens(primary_pages_normalized, cfg)
    glyph_hv = _high_value_tokens(glyph_pages, cfg)
    return bool(glyph_hv - primary_hv)


__all__ = ["glyph_page_texts", "looks_incomplete"]
