"""Stage 6 — raster / pixel forensics (``image_forensics``).

Pixel-level tamper detection for scanned / photographed bills — the pages where
the digital-native detectors (Stages 1–4) have no reliable embedded text or font
structure to analyse. Stage 3 (``ocr_crosscheck``) routes those pages here via
``routed_to="image_forensics"``; this package's name matches that route string by
design (see ``docs/STAGE6_DESIGN.md``).

Session 6.1 ships the SCAFFOLDING only — embedded-image extraction
(:mod:`.images`), the per-page activation predicate (:mod:`.activation`), and the
engine-agnostic forensic abstraction (:mod:`.engine`). No detectors, no scoring,
and not yet wired into the live ``STAGES`` list.
"""

from __future__ import annotations

#: Canonical stage identity — matches Stage 3's ``routed_to`` hand-off string.
STAGE_NAME = "image_forensics"

from .activation import (  # noqa: E402
    DocumentActivation,
    PageActivation,
    activate,
    activate_page,
)
from .config import ImageForensicsConfig  # noqa: E402
from .engine import (  # noqa: E402
    ClassicalProvider,
    ForensicMap,
    ForensicMethod,
    ForensicProvenance,
    ForensicProvider,
    PhotoHolmesProvider,
    StubForensicProvider,
    default_provider,
)
from .images import DecodedImage, decoded_images, extract_images  # noqa: E402

__all__ = [
    "STAGE_NAME",
    "ImageForensicsConfig",
    "DecodedImage",
    "decoded_images",
    "extract_images",
    "PageActivation",
    "DocumentActivation",
    "activate",
    "activate_page",
    "ForensicMap",
    "ForensicProvenance",
    "ForensicMethod",
    "ForensicProvider",
    "ClassicalProvider",
    "PhotoHolmesProvider",
    "StubForensicProvider",
    "default_provider",
]
