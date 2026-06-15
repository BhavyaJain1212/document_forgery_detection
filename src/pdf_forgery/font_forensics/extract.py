"""Per-character glyph extraction and line/token grouping for font forensics.

The actual extraction + grouping primitives live in the stage-agnostic
:mod:`pdf_forgery.core.glyphs` so they can be shared with revision-recovery's
fallback text extractor (no duplicated glyph logic across stages). This module
is a thin, stage-flavoured surface over them:

    - it re-exports the pure extractors / helpers unchanged, and
    - it wraps :func:`core.glyphs.group_lines` so callers can keep passing a
      :class:`FontConfig` (whose ``line_baseline_tolerance`` / ``token_gap_ratio``
      drive the grouping) instead of the raw keyword arguments.

All functions remain tolerant: malformed input yields ``[]`` rather than raising.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..core.glyphs import (
    Glyph,
    TextLine,
    distinct_fonts,
    dominant_font,
    glyphs_from_bytes,
    glyphs_from_layouts,
)
from ..core.glyphs import group_lines as _core_group_lines
from .config import FontConfig

__all__ = [
    "glyphs_from_bytes",
    "glyphs_from_layouts",
    "group_lines",
    "dominant_font",
    "distinct_fonts",
]


def group_lines(glyphs: Iterable[Glyph], config: FontConfig | None = None) -> list[TextLine]:
    """Cluster glyphs into lines/tokens using a :class:`FontConfig`'s thresholds.

    Thin wrapper over :func:`pdf_forgery.core.glyphs.group_lines` that maps the
    stage config onto the shared extractor's keyword arguments.
    """
    cfg = config or FontConfig()
    return _core_group_lines(
        glyphs,
        line_baseline_tolerance=cfg.line_baseline_tolerance,
        token_gap_ratio=cfg.token_gap_ratio,
    )
