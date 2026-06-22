"""Detector orchestration (Stage 6, §5–§7 — combination only, no signal math).

This is the CONSUMER layer: it runs the engine's methods over each image-dominant
page's embedded images, turns each ``ForensicMap`` into localized blobs
(:mod:`.localize`), suppresses the design's named false positives, and combines
co-located fires into provisional tamper regions. It deliberately does **no**
signal processing — the ELA / DQ / noise / copy-move math lives behind the
``ForensicProvider`` Protocol, and this module depends only on that Protocol
(swap the provider, this layer is unchanged).

What it emits is a provisional, typed region list — **not** a ``StageResult``.
The tier/score rule tree and the ``core.Stage`` wiring are Session 6.3; this
session stops at "here are the surviving regions, which methods back each, are
they co-located, and do they sit in the high-value band."

False-positive suppression (§5 FP column / §7 LOW rule):

* **whole-image recompression / uniform scanner artifact** — a method whose
  thresholded heatmap (or single blob) covers ``>= cfg.global_coverage_frac`` of
  the image is diffuse, not a local edit: recorded as a ``global_signal``, never
  promoted to a region.
* **speckle** — blobs ``< cfg.min_blob_area_frac`` of the image are dropped in
  :mod:`.localize`.

Corroboration (§7): two fires from DIFFERENT methods whose page-point boxes
overlap by ``>= cfg.colocate_iou`` are co-located; a region carrying ``>= 2``
distinct methods is flagged ``co_located`` (the gate 6.3 turns into HIGH — e.g. a
DQ ghost AND a noise break over the same region). A lone method is never
co-located (caps at MEDIUM in 6.3).

A method that ERRORS on an image-dominant page is recorded in ``method_errors``,
never silently dropped (mirrors revision_recovery recon-failure → MEDIUM). With
the 6.1 classical methods still raising ``NotImplementedError``, running this
against the real ``ClassicalProvider`` degrades to all-errors + no regions rather
than crashing — exactly the contract.

PHI: heatmaps and pixels never leave the engine; this module handles only
positions / areas / method names / hashes (§8). It logs nothing by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .activation import DocumentActivation, activate
from .config import ImageForensicsConfig
from .engine import ForensicMap, ForensicProvenance, ForensicProvider, default_provider
from .images import DecodedImage, decoded_images
from .localize import (
    PagePointBBox,
    blob_to_page_bbox,
    heatmap_blobs,
    hot_fraction,
    iou,
    overlaps_high_value,
    union_bbox,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..core.context import AnalysisContext


# --------------------------------------------------------------------------- #
# Provisional records (NOT core.StageResult — that mapping is 6.3)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MethodFire:
    """One method's localized blob on one image — a single suspicious region."""

    page_index: int
    method: str
    version: str
    page_bbox: PagePointBBox | None
    area_frac: float
    strength: float
    page_width_pt: float | None = None
    page_height_pt: float | None = None
    xobject_id: str | None = None


@dataclass(frozen=True)
class GlobalSignal:
    """A diffuse / whole-image fire — recorded (consistent with rescan), not a region."""

    page_index: int
    method: str
    version: str
    coverage: float          # fraction of the image above threshold
    strength: float


@dataclass(frozen=True)
class MethodError:
    """A method that raised on an image-dominant page — never silently dropped."""

    page_index: int
    method: str
    reason: str              # exception class name, never a message (PHI-safe)


@dataclass(frozen=True)
class CapabilityGap:
    """A method whose implementation is not yet available (``NotImplementedError``).

    Distinct from :class:`MethodError`: a capability gap means the method could not
    even attempt the analysis (the classical pixel math is deferred — Session 6.2
    decision), NOT that an implemented method failed at runtime. The scorer treats
    a gap as "no signal" (→ INCONCLUSIVE), so registering the stage live while the
    DSP is deferred can never manufacture a false MEDIUM. A genuine runtime error
    on an implemented method is a :class:`MethodError` (→ MEDIUM)."""

    page_index: int
    method: str


@dataclass(frozen=True)
class TamperRegion:
    """A combined provisional region: which methods back it, co-located?, high-value?"""

    page_index: int
    page_bbox: PagePointBBox | None
    methods: tuple[str, ...]          # distinct contributing method names, sorted
    strength: float                   # max contributing fire strength
    co_located: bool                  # >= 2 independent methods over this region
    high_value: bool                  # overlaps the positional amount band (§4)
    page_width_pt: float | None = None
    page_height_pt: float | None = None
    note: str | None = None


@dataclass(frozen=True)
class DetectionResult:
    """Everything the 6.3 scorer needs, with the engine's reproducibility manifest."""

    regions: tuple[TamperRegion, ...] = ()
    fires: tuple[MethodFire, ...] = ()
    global_signals: tuple[GlobalSignal, ...] = ()
    method_errors: tuple[MethodError, ...] = ()
    capability_gaps: tuple[CapabilityGap, ...] = ()
    executions: int = 0
    analyzed_pages: tuple[int, ...] = ()
    provider: str = ""
    provenance: ForensicProvenance | None = None

    @property
    def any_region(self) -> bool:
        return bool(self.regions)

    @property
    def any_co_located(self) -> bool:
        return any(r.co_located for r in self.regions)

    @property
    def analyzed(self) -> bool:
        """True if at least one method actually executed (ran or errored).

        Counts methods that ran to completion (``executions`` — even with no fire:
        "analysed, found nothing" is a real LOW signal, not no-signal) and runtime
        errors. Capability gaps (deferred / unavailable methods) do NOT count — a
        page with only gaps was never really analysed, so the scorer keeps it
        INCONCLUSIVE rather than inventing a verdict from nothing."""
        return bool(
            self.executions
            or self.fires
            or self.global_signals
            or self.method_errors
        )


# --------------------------------------------------------------------------- #
# Per-method threshold lookup (all values from config)
# --------------------------------------------------------------------------- #

def method_threshold(cfg: ImageForensicsConfig, method: str) -> float:
    """The configured heatmap threshold for ``method`` (default 0.50)."""
    return {
        "ela": cfg.ela_threshold,
        "double_jpeg": cfg.dq_threshold,
        "jpeg_grid": cfg.jpeg_grid_threshold,
        "noise_inconsistency": cfg.noise_threshold,
    }.get(method, 0.50)


# --------------------------------------------------------------------------- #
# Top-level entry
# --------------------------------------------------------------------------- #

def detect(
    ctx: "AnalysisContext",
    *,
    provider: ForensicProvider | None = None,
    config: ImageForensicsConfig | None = None,
    activation: DocumentActivation | None = None,
) -> DetectionResult:
    """Run the provider over image-dominant pages → provisional tamper regions.

    Read-only and never raises. With no image-dominant page the result is empty
    (INCONCLUSIVE in 6.3). The provider defaults to :func:`default_provider`
    (classical when available, else stub); pass a controllable provider in tests.
    """
    cfg = config or ImageForensicsConfig()
    prov = provider or default_provider(cfg)
    act = activation or activate(ctx, cfg)
    images = decoded_images(ctx, cfg)

    dominant = set(act.image_dominant_pages)
    targets = [im for im in images if im.page_index in dominant]

    fires, globals_, errors, gaps, executions = _run_methods(prov, targets, cfg)
    regions = combine_fires(fires, cfg)

    return DetectionResult(
        regions=tuple(regions),
        fires=tuple(fires),
        global_signals=tuple(globals_),
        method_errors=tuple(errors),
        capability_gaps=tuple(gaps),
        executions=executions,
        analyzed_pages=tuple(sorted(dominant)),
        provider=getattr(prov, "name", ""),
        provenance=_provenance(prov, cfg),
    )


def _run_methods(
    prov: ForensicProvider,
    images: list[DecodedImage],
    cfg: ImageForensicsConfig,
) -> tuple[list[MethodFire], list[GlobalSignal], list[MethodError], list[CapabilityGap], int]:
    """Run every applicable method over every image; localize / suppress each map."""
    fires: list[MethodFire] = []
    globals_: list[GlobalSignal] = []
    errors: list[MethodError] = []
    gaps: list[CapabilityGap] = []
    executions = 0

    try:
        methods = prov.methods(cfg)
    except Exception:
        methods = []

    for image in images:
        for method in methods:
            try:
                if not method.applicable(image, cfg):
                    continue
            except Exception:
                continue
            try:
                fmap = method.analyze(image, cfg)
            except NotImplementedError:
                # Capability not yet available (deferred classical DSP) — NOT a
                # runtime failure. Recorded as a gap → no signal (INCONCLUSIVE),
                # so a stub-only provider can never manufacture a false MEDIUM.
                gaps.append(CapabilityGap(image.page_index, method.name))
                continue
            except Exception as exc:
                # A genuine error in an IMPLEMENTED method — never silently
                # dropped; surfaces as MEDIUM in scoring.
                errors.append(
                    MethodError(image.page_index, method.name, type(exc).__name__)
                )
                continue
            # The method executed (even a None / empty heatmap is a real
            # "analysed, found nothing" outcome → LOW, not no-signal).
            executions += 1
            f, g = _fires_from_map(fmap, image, cfg)
            fires.extend(f)
            globals_.extend(g)
    return fires, globals_, errors, gaps, executions


def _fires_from_map(
    fmap: ForensicMap,
    image: DecodedImage,
    cfg: ImageForensicsConfig,
) -> tuple[list[MethodFire], list[GlobalSignal]]:
    """Turn one ``ForensicMap`` into localized fires + any global/diffuse signal."""
    thr = method_threshold(cfg, fmap.method)

    if fmap.heatmap is None:
        # Scalar-only method: cannot localize. A firing scalar is a diffuse signal
        # (uncorroboratable on its own) — recorded as global, never a region.
        if fmap.scalar is not None and fmap.scalar >= thr:
            return [], [
                GlobalSignal(
                    image.page_index, fmap.method, fmap.version, 1.0, float(fmap.scalar)
                )
            ]
        return [], []

    # Whole-image lift (recompression / uniform scanner artifact) → not local.
    coverage = hot_fraction(fmap.heatmap, thr)
    if coverage >= cfg.global_coverage_frac:
        peak = _peak(fmap)
        return [], [
            GlobalSignal(image.page_index, fmap.method, fmap.version, coverage, peak)
        ]

    fires: list[MethodFire] = []
    globals_: list[GlobalSignal] = []
    for blob in heatmap_blobs(
        fmap.heatmap, threshold=thr, min_area_frac=cfg.min_blob_area_frac
    ):
        # A single blob that still covers most of the image is itself global.
        if blob.area_frac >= cfg.global_coverage_frac:
            globals_.append(
                GlobalSignal(
                    image.page_index, fmap.method, fmap.version, blob.area_frac, blob.peak
                )
            )
            continue
        fires.append(
            MethodFire(
                page_index=image.page_index,
                method=fmap.method,
                version=fmap.version,
                page_bbox=blob_to_page_bbox(blob, image),
                area_frac=blob.area_frac,
                strength=blob.peak,
                page_width_pt=image.page_width_pt,
                page_height_pt=image.page_height_pt,
                xobject_id=image.xobject_id,
            )
        )
    return fires, globals_


# --------------------------------------------------------------------------- #
# Corroboration — cluster co-located fires into regions
# --------------------------------------------------------------------------- #

@dataclass
class _RegionBuilder:
    page_index: int
    bbox: PagePointBBox | None
    methods: set[str] = field(default_factory=set)
    strength: float = 0.0
    page_width_pt: float | None = None
    page_height_pt: float | None = None

    def add(self, f: MethodFire) -> None:
        self.methods.add(f.method)
        self.strength = max(self.strength, f.strength)
        if f.page_bbox is not None:
            self.bbox = f.page_bbox if self.bbox is None else union_bbox(self.bbox, f.page_bbox)
        if self.page_width_pt is None:
            self.page_width_pt = f.page_width_pt
        if self.page_height_pt is None:
            self.page_height_pt = f.page_height_pt


def combine_fires(
    fires: list[MethodFire], cfg: ImageForensicsConfig
) -> list[TamperRegion]:
    """Greedily merge fires that are co-located (IoU ``>=`` threshold) on a page.

    Two fires join a region when their page boxes overlap by ``>= cfg.colocate_iou``.
    A region with ``>= 2`` distinct methods is ``co_located`` (the §7 HIGH gate).
    Fires with no page box (unlocatable) each become their own single-method
    region so they are never dropped.
    """
    builders: list[_RegionBuilder] = []
    for f in fires:
        placed = False
        if f.page_bbox is not None:
            for b in builders:
                if (
                    b.page_index == f.page_index
                    and b.bbox is not None
                    and iou(f.page_bbox, b.bbox) >= cfg.colocate_iou
                ):
                    b.add(f)
                    placed = True
                    break
        if not placed:
            nb = _RegionBuilder(page_index=f.page_index, bbox=None)
            nb.add(f)
            builders.append(nb)

    regions: list[TamperRegion] = []
    for b in builders:
        co_located = len(b.methods) >= 2
        high_value = (
            b.bbox is not None
            and overlaps_high_value(b.bbox, b.page_height_pt, cfg)
        )
        regions.append(
            TamperRegion(
                page_index=b.page_index,
                page_bbox=b.bbox,
                methods=tuple(sorted(b.methods)),
                strength=b.strength,
                co_located=co_located,
                high_value=high_value,
                page_width_pt=b.page_width_pt,
                page_height_pt=b.page_height_pt,
            )
        )
    return regions


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _peak(fmap: ForensicMap) -> float:
    try:
        import numpy as np

        if fmap.heatmap is not None:
            return float(np.asarray(fmap.heatmap, dtype=np.float64).max())
    except Exception:
        pass
    return float(fmap.scalar) if fmap.scalar is not None else 0.0


def _provenance(
    prov: ForensicProvider, cfg: ImageForensicsConfig
) -> ForensicProvenance | None:
    try:
        return prov.provenance(cfg)
    except Exception:
        return None


__all__ = [
    "MethodFire",
    "GlobalSignal",
    "MethodError",
    "CapabilityGap",
    "TamperRegion",
    "DetectionResult",
    "method_threshold",
    "detect",
    "combine_fires",
]
