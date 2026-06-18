"""Pure data models for Stage 3 (OCR ↔ embedded-text cross-check).

These are the *contract* between the stage's internals and the rest of the
pipeline. They carry NO detection logic — they are frozen dataclasses / enums
only, exactly like the data models in the sibling stages.

See ``docs/STAGE3_DESIGN.md`` for the full design rationale. The token-class
vocabulary deliberately maps from the existing
``revision_recovery.highvalue.HighValueKind`` so Stage 3 never grows a second,
divergent token classifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..core.types import ConfidenceTier


class WordSource(str, Enum):
    """Which extraction path a :class:`WordBox` came from."""

    EMBEDDED = "embedded"
    """pdfminer embedded text layer (via ``core.glyphs`` / ``page_layouts``)."""

    OCR = "ocr"
    """Text recovered from the rendered raster by the OCR engine."""


class DivergenceType(str, Enum):
    """The four mutually-exclusive outcomes of comparing the two sources.

    Defined and weighted in ``docs/STAGE3_DESIGN.md`` §2.
    """

    AGREE = "agree"
    """Spatially matched and textually equal within tolerance (weight 0)."""

    MISMATCH = "mismatch"
    """Spatially matched but text differs beyond tolerance — image-layer overlay
    or hidden-text swap. Strongest signal."""

    EMBEDDED_ONLY = "embedded_only"
    """Embedded word with no rendered counterpart — hidden / invisible text."""

    OCR_ONLY = "ocr_only"
    """Rendered word with no embedded counterpart — image-layer overlay."""


class TokenClass(str, Enum):
    """High-value class of a token, mapped from ``highvalue.HighValueKind``.

    Drives both the STRICTER tolerance (§3c inversion) and the weight multiplier
    (§6a). ``PROSE`` is the default non-high-value class.
    """

    AMOUNT = "amount"
    DATE = "date"
    ID = "id"
    PROSE = "prose"


@dataclass(frozen=True)
class WordBox:
    """One word with its bbox, source, and (for OCR) confidence.

    ``bbox`` is always in PIXEL space with a top-left origin
    (``x0, y0, x1, y1``, ``y0 < y1``). Embedded words are transformed into pixel
    space (``align.embedded_to_pixel``) before being stored, so embedded and OCR
    boxes are directly comparable.
    """

    text: str
    bbox: tuple[float, float, float, float]
    source: WordSource
    conf: float | None
    page_index: int


@dataclass(frozen=True)
class Divergence:
    """One classified comparison outcome backing a finding.

    For a one-to-many match (one OCR detection box covering several embedded
    words) ``embedded`` holds all embedded words in the group; ``ocr`` is the
    single OCR box (``None`` for an ``EMBEDDED_ONLY`` divergence).
    """

    type: DivergenceType
    embedded: tuple[WordBox, ...]
    ocr: WordBox | None
    token_class: TokenClass
    weight: float
    page_index: int


@dataclass(frozen=True)
class RenderProvenance:
    """Reproducibility record: how the raster + OCR were produced.

    Recorded on every report so a result can be reproduced exactly — engine
    identity, model version, language, device, and the DPI actually rendered at.
    """

    engine: str
    model_version: str
    language: str
    device: str
    render_dpi: int


@dataclass(frozen=True)
class Stage3Result:
    """The compact cross-check verdict (maps onto ``core.StageResult``).

    ``routed_to`` is set (e.g. ``"image_forensics"``) when the scanned /
    text-sparse short-circuit fired (§5); ``None`` on the normal digital-native
    path.
    """

    tier: ConfidenceTier
    score: int | None
    divergences: tuple[Divergence, ...]
    routed_to: str | None = None


@dataclass(frozen=True)
class OCRCrossCheckReport:
    """Rich per-file payload carried as ``StageResult.payload``.

    Holds everything the adapter needs to render the original JSON / human
    summary without the core schema modelling Stage 3's internals — mirroring the
    ``payload`` pattern used by every other stage.
    """

    path: str
    ok: bool
    result: Stage3Result | None = None
    provenance: RenderProvenance | None = None
    error: str | None = None
    #: PHI-safe diagnostics (counts only): off-page words dropped by the clipping
    #: guard, OCR words dropped below the confidence floor, matched/agree counts.
    diagnostics: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    page_dims_px: tuple[tuple[float, float], ...] = ()
    """Per-page ``(width_px, height_px)`` at ``render_dpi``, indexed by
    ``page_index``.  Already rotation-aware (pypdfium2 swaps dims for 90/270).
    Used by the aggregate layer to normalize pixel-space bboxes."""


__all__ = [
    "WordSource",
    "DivergenceType",
    "TokenClass",
    "WordBox",
    "Divergence",
    "RenderProvenance",
    "Stage3Result",
    "OCRCrossCheckReport",
]
