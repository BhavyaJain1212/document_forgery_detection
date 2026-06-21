"""Activation predicate (Stage 6, §1) — decide per page whether forensics apply.

Stage 6 is SUBSTANTIVE only on **image-dominant** pages (scanned / photographed
bills); on digital-native pages it contributes nothing (→ INCONCLUSIVE to fusion,
read as *no signal*). A page is image-dominant when **either**:

* **Text floor** — its embedded word count ``< cfg.min_embedded_words`` (the same
  constant Stage 3 uses, so the two stages partition the document coherently), OR
* **Image dominance** — a single decoded raster image covers
  ``>= cfg.image_area_dominance_frac`` of the page area.

The embedded-word signal is read from the SHARED context's already-cached
``page_layouts`` via ``core.glyphs`` (the one shared extractor) — this module
never re-parses the file or re-implements text extraction (mirroring how Stage 3
reuses the same signal). Pixel forensics are **never** run on
``ctx.rasterized_pages()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..core.glyphs import glyphs_from_layouts, group_lines
from .config import ImageForensicsConfig
from .images import DecodedImage, decoded_images

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..core.context import AnalysisContext


@dataclass(frozen=True)
class PageActivation:
    """Activation outcome for one page."""

    page_index: int
    embedded_words: int
    max_image_coverage: float
    image_dominant: bool
    reason: str


@dataclass(frozen=True)
class DocumentActivation:
    """Whole-document activation roll-up (§1)."""

    pages: tuple[PageActivation, ...]

    @property
    def any_image_dominant(self) -> bool:
        """True when at least one page is image-dominant (stage analyses those)."""
        return any(p.image_dominant for p in self.pages)

    @property
    def image_dominant_pages(self) -> tuple[int, ...]:
        """Indices of the pages the stage should analyse."""
        return tuple(p.page_index for p in self.pages if p.image_dominant)


# --------------------------------------------------------------------------- #
# Predicate
# --------------------------------------------------------------------------- #

def activate(
    ctx: "AnalysisContext", config: ImageForensicsConfig | None = None
) -> DocumentActivation:
    """Classify every page analyse/skip; never raises (empty doc → no pages)."""
    cfg = config or ImageForensicsConfig()
    word_counts = embedded_word_counts(ctx)
    images = decoded_images(ctx, cfg)

    images_by_page: dict[int, list[DecodedImage]] = {}
    for img in images:
        images_by_page.setdefault(img.page_index, []).append(img)

    n_pages = max(len(word_counts), (max(images_by_page) + 1) if images_by_page else 0)
    pages = tuple(
        activate_page(
            page_index=i,
            embedded_words=word_counts.get(i, 0),
            images=images_by_page.get(i, []),
            config=cfg,
        )
        for i in range(n_pages)
    )
    return DocumentActivation(pages=pages)


def activate_page(
    *,
    page_index: int,
    embedded_words: int,
    images: list[DecodedImage],
    config: ImageForensicsConfig | None = None,
) -> PageActivation:
    """Apply the per-page predicate to pre-computed signals (pure / testable)."""
    cfg = config or ImageForensicsConfig()
    max_cov = max_image_coverage(images)

    text_floor = embedded_words < cfg.min_embedded_words
    image_dominance = max_cov >= cfg.image_area_dominance_frac
    dominant = text_floor or image_dominance

    if not dominant:
        reason = "digital-native (text-rich, no dominant image)"
    elif text_floor and image_dominance:
        reason = "image-dominant (text floor + image coverage)"
    elif text_floor:
        reason = f"image-dominant (text floor: {embedded_words} < {cfg.min_embedded_words} words)"
    else:
        reason = f"image-dominant (image covers {max_cov:.0%} of page)"

    return PageActivation(
        page_index=page_index,
        embedded_words=embedded_words,
        max_image_coverage=max_cov,
        image_dominant=dominant,
        reason=reason,
    )


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #

def embedded_word_counts(ctx: "AnalysisContext") -> dict[int, int]:
    """Embedded word count per 0-based page index, from the shared context.

    Uses ``core.glyphs`` (the ONE shared extractor) over the already-cached
    ``ctx.page_layouts`` — no second text-extraction path. A page with no
    extractable text simply maps to ``0``.
    """
    counts: dict[int, int] = {}
    try:
        layouts = ctx.page_layouts
    except Exception:
        return counts
    # Seed every page at 0 so blank pages are represented in the rollup.
    for i in range(len(layouts)):
        counts[i] = 0
    glyphs = glyphs_from_layouts(layouts)
    for line in group_lines(glyphs):
        counts[line.page_index] = counts.get(line.page_index, 0) + len(line.tokens)
    return counts


def max_image_coverage(images: list[DecodedImage]) -> float:
    """Largest single-image page-area coverage fraction among ``images``.

    Coverage uses the image's placement rectangle (in points) over the page area;
    images without a known placement or page area contribute ``0`` (a missing box
    must never inflate dominance). Returns ``0.0`` when there are no images.
    """
    best = 0.0
    for img in images:
        frac = _coverage_frac(img)
        if frac > best:
            best = frac
    return best


def _coverage_frac(img: DecodedImage) -> float:
    if (
        img.placement is None
        or img.page_width_pt is None
        or img.page_height_pt is None
    ):
        return 0.0
    page_area = img.page_width_pt * img.page_height_pt
    if page_area <= 0:
        return 0.0
    x0, top0, x1, top1 = img.placement
    rect_area = abs(x1 - x0) * abs(top1 - top0)
    return min(rect_area / page_area, 1.0)


__all__ = [
    "PageActivation",
    "DocumentActivation",
    "activate",
    "activate_page",
    "embedded_word_counts",
    "max_image_coverage",
]
