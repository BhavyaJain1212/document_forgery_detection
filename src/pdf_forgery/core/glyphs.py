"""Shared, stage-agnostic per-character glyph extraction and line/token grouping.

Several stages need pdfminer's PER-CHARACTER attribution (``LTChar.fontname`` /
size / bbox) rather than page-level font lists:

    - ``font_forensics`` compares per-glyph fonts to spot re-embedded edits;
    - ``revision_recovery`` uses the same glyph path as a FALLBACK text extractor
      when its primary (container-level) extraction comes back suspiciously
      incomplete.

To avoid two divergent copies of this logic, the extraction primitives and the
pure :class:`Glyph` / :class:`Token` / :class:`TextLine` data models live here in
``core`` (which depends on no stage) and both stages import them.

All functions are tolerant: malformed input yields ``[]`` rather than raising,
matching the project-wide "report and continue, never crash" constraint.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from io import BytesIO
from statistics import median
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pdfminer.layout import LTPage

# Grouping defaults (kept here so the shared extractor has no stage-config
# dependency; stages may pass overrides via the keyword arguments below).
DEFAULT_LINE_BASELINE_TOLERANCE = 0.5
DEFAULT_TOKEN_GAP_RATIO = 0.35


# ---------------------------------------------------------------------------
# Pure data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Glyph:
    """One rendered character with its pdfminer font attribution and bbox.

    Coordinates are pdfminer's (PDF user space, origin bottom-left). ``fontname``
    is taken verbatim from ``LTChar.fontname`` — page-level font lists are never
    consulted, so per-character switches are preserved.
    """

    text: str
    fontname: str
    size: float
    x0: float
    y0: float
    x1: float
    y1: float
    page_index: int

    @property
    def is_space(self) -> bool:
        """True for whitespace glyphs (token separators, never font-compared)."""
        return self.text.isspace() or self.text == ""


@dataclass(frozen=True)
class Token:
    """A whitespace-delimited run of glyphs on one line."""

    text: str
    glyphs: tuple[Glyph, ...]

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """``(x0, y0, x1, y1)`` enclosing the token's glyphs."""
        xs0 = [g.x0 for g in self.glyphs]
        ys0 = [g.y0 for g in self.glyphs]
        xs1 = [g.x1 for g in self.glyphs]
        ys1 = [g.y1 for g in self.glyphs]
        return (min(xs0), min(ys0), max(xs1), max(ys1))


@dataclass(frozen=True)
class TextLine:
    """A horizontally-grouped line of glyphs on a single page."""

    page_index: int
    glyphs: tuple[Glyph, ...]
    tokens: tuple[Token, ...]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _iter_chars(obj: object) -> Iterator[object]:
    """Yield every ``LTChar`` in a pdfminer layout subtree (depth-first)."""
    from pdfminer.layout import LTChar

    if isinstance(obj, LTChar):
        yield obj
        return
    if isinstance(obj, Iterable):
        for child in obj:  # LTPage / LTTextBox / LTTextLine are iterable
            yield from _iter_chars(child)


def glyphs_from_layouts(layouts: list["LTPage"]) -> list[Glyph]:
    """Flatten pdfminer page layouts to a flat, page-indexed list of glyphs."""
    from pdfminer.layout import LTChar

    glyphs: list[Glyph] = []
    for page_index, page in enumerate(layouts):
        for ch in _iter_chars(page):
            if not isinstance(ch, LTChar):
                continue
            try:
                glyphs.append(
                    Glyph(
                        text=ch.get_text(),
                        fontname=ch.fontname or "",
                        size=float(ch.size),
                        x0=float(ch.x0),
                        y0=float(ch.y0),
                        x1=float(ch.x1),
                        y1=float(ch.y1),
                        page_index=page_index,
                    )
                )
            except Exception:  # a degenerate glyph never aborts extraction
                continue
    return glyphs


def glyphs_from_bytes(data: bytes) -> list[Glyph]:
    """Extract glyphs directly from PDF bytes (when no shared context exists)."""
    try:
        from pdfminer.high_level import extract_pages

        layouts = list(extract_pages(BytesIO(data)))
    except Exception:  # malformed / encrypted / corrupt: never crash
        return []
    return glyphs_from_layouts(layouts)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def group_lines(
    glyphs: Iterable[Glyph],
    *,
    line_baseline_tolerance: float = DEFAULT_LINE_BASELINE_TOLERANCE,
    token_gap_ratio: float = DEFAULT_TOKEN_GAP_RATIO,
) -> list[TextLine]:
    """Cluster glyphs into horizontal lines, then split each line into tokens.

    Lines are formed per page by baseline (``y0``) proximity; within a line the
    glyphs are ordered left-to-right and split into tokens on space glyphs or
    wide horizontal gaps. Deterministic and independent of pdfminer's LAParams
    line boxes so behaviour is stable and unit-testable.
    """
    by_page: dict[int, list[Glyph]] = {}
    for g in glyphs:
        by_page.setdefault(g.page_index, []).append(g)

    lines: list[TextLine] = []
    for page_index in sorted(by_page):
        lines.extend(
            _group_page_lines(
                page_index, by_page[page_index], line_baseline_tolerance, token_gap_ratio
            )
        )
    return lines


def _group_page_lines(
    page_index: int,
    glyphs: list[Glyph],
    line_baseline_tolerance: float,
    token_gap_ratio: float,
) -> list[TextLine]:
    if not glyphs:
        return []
    sizes = [g.size for g in glyphs if g.size > 0] or [1.0]
    tol = line_baseline_tolerance * median(sizes)

    # Sort top-to-bottom (descending baseline), stable on x for determinism.
    ordered = sorted(glyphs, key=lambda g: (-g.y0, g.x0))

    clusters: list[list[Glyph]] = []
    refs: list[float] = []  # running baseline reference per cluster
    for g in ordered:
        placed = False
        for i, ref in enumerate(refs):
            if abs(g.y0 - ref) <= tol:
                clusters[i].append(g)
                # Average the reference so a slowly-drifting baseline stays one line.
                refs[i] = (ref * (len(clusters[i]) - 1) + g.y0) / len(clusters[i])
                placed = True
                break
        if not placed:
            clusters.append([g])
            refs.append(g.y0)

    lines: list[TextLine] = []
    for cluster in clusters:
        row = sorted(cluster, key=lambda g: g.x0)
        tokens = _split_tokens(row, token_gap_ratio)
        lines.append(
            TextLine(page_index=page_index, glyphs=tuple(row), tokens=tuple(tokens))
        )
    # Deterministic order: top-to-bottom by the cluster's first glyph baseline.
    lines.sort(key=lambda ln: (-ln.glyphs[0].y0, ln.glyphs[0].x0))
    return lines


def _split_tokens(row: list[Glyph], token_gap_ratio: float) -> list[Token]:
    """Split a left-to-right glyph row into whitespace-delimited tokens."""
    tokens: list[Token] = []
    current: list[Glyph] = []
    prev: Glyph | None = None

    def flush() -> None:
        if current:
            tokens.append(
                Token(text="".join(g.text for g in current), glyphs=tuple(current))
            )

    for g in row:
        if g.is_space:
            flush()
            current = []
            prev = None
            continue
        if prev is not None:
            gap = g.x0 - prev.x1
            ref = max(prev.size, g.size, 1.0)
            if gap > token_gap_ratio * ref:
                flush()
                current = []
        current.append(g)
        prev = g
    flush()
    return tokens


def dominant_font(glyphs: Iterable[Glyph]) -> str | None:
    """Most common non-space fontname among *glyphs*; ``None`` if none.

    Ties are broken by fontname for determinism.
    """
    counts: Counter[str] = Counter(g.fontname for g in glyphs if not g.is_space)
    if not counts:
        return None
    top = max(counts.values())
    winners = sorted(name for name, c in counts.items() if c == top)
    return winners[0]


def distinct_fonts(glyphs: Iterable[Glyph]) -> tuple[str, ...]:
    """Sorted tuple of distinct non-space fontnames present in *glyphs*."""
    return tuple(sorted({g.fontname for g in glyphs if not g.is_space}))


__all__ = [
    "Glyph",
    "Token",
    "TextLine",
    "glyphs_from_layouts",
    "glyphs_from_bytes",
    "group_lines",
    "dominant_font",
    "distinct_fonts",
]
