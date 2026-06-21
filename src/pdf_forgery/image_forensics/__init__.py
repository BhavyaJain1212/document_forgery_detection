"""Stage 6 — raster / pixel forensics (``image_forensics``).

Pixel-level tamper detection for scanned / photographed bills — the pages where
the digital-native detectors (Stages 1–4) have no reliable embedded text or font
structure to analyse. Stage 3 (``ocr_crosscheck``) routes those pages here via
``routed_to="image_forensics"``; this package's name matches that route string by
design (see ``docs/STAGE6_DESIGN.md``).

Status: extraction (:mod:`.images`), activation (:mod:`.activation`), the
engine-agnostic forensic abstraction (:mod:`.engine`), the detector/localizer
(:mod:`.detect`, :mod:`.localize`), the §7 scoring rule tree (:mod:`.scoring`),
and the pipeline :class:`~.stage.ImageForensicsStage` are all in place and the
stage is registered in the live pipeline as SUBSTANTIVE. The one piece still
DEFERRED is the real classical pixel math: ``ClassicalProvider``'s methods raise
``NotImplementedError`` (recorded as capability gaps → no signal), so the stage
is INCONCLUSIVE on every document until that DSP lands — safe to run live.
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
from .detect import (  # noqa: E402
    DetectionResult,
    GlobalSignal,
    MethodError,
    MethodFire,
    TamperRegion,
    combine_fires,
    detect,
    method_threshold,
)
from .localize import (  # noqa: E402
    Blob,
    blob_to_page_bbox,
    heatmap_blobs,
    hot_fraction,
    iou,
    overlaps_high_value,
)
from .scoring import (  # noqa: E402
    ImageForensicsReport,
    RegionFinding,
    score,
)
from .stage import (  # noqa: E402
    ImageForensicsStage,
    report_to_stage_result,
    stage_result_to_report,
)

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
    # Session 6.2 — detectors + localization
    "DetectionResult",
    "TamperRegion",
    "MethodFire",
    "GlobalSignal",
    "MethodError",
    "detect",
    "combine_fires",
    "method_threshold",
    "Blob",
    "heatmap_blobs",
    "hot_fraction",
    "blob_to_page_bbox",
    "iou",
    "overlaps_high_value",
    # Session 6.3 — scoring + Stage
    "ImageForensicsReport",
    "RegionFinding",
    "score",
    "ImageForensicsStage",
    "report_to_stage_result",
    "stage_result_to_report",
]
