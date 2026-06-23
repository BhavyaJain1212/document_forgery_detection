"""Scoring rule tree (Stage 6, §7) — ``DetectionResult`` → tier / score / report.

Maps the 6.2 detector output onto the shared :class:`core.ConfidenceTier`
semantics. Conservative by design (scanned documents are inherently noisy) and
deliberately unable to originate HIGH from a single method — the §7 rule tree:

```
INCONCLUSIVE — no image-dominant page (digital-native; defers to Stages 1-4), OR
               image-dominant page(s) exist but NO method actually executed
               (only capability gaps — the deferred classical DSP). "No signal";
               must never contend with the parse-side stages.

LOW          — analysed, and only weak / diffuse evidence: a global (whole-image)
               signal consistent with an innocent rescan, a cluster of scattered
               lone regions from one method, a single weak isolated blob below
               the MEDIUM strength floor, or nothing fired.

MEDIUM       — exactly one of: a lone (uncorroborated) region above the strength
               floor, a corroborated but page-spanning/non-local region, OR a
               method ERRORED on an image-dominant page (never silently dropped).
               Capped — these signals never reach HIGH here; cross-stage
               corroboration may lift them in fusion (§7 fusion role).

HIGH         — at least one region with TWO independent, co-located methods over
               the same LOCAL area (e.g. a DQ ghost AND a co-located noise
               break). The only path to HIGH within this stage.
```

The stage tier is the worst-case across analysed pages (the per-page → document
rollup used elsewhere). PHI: this module sees only the provisional regions
(positions / strengths / method names) — never pixels or heatmaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.types import ConfidenceTier
from .activation import DocumentActivation
from .config import ImageForensicsConfig
from .detect import DetectionResult, TamperRegion


@dataclass(frozen=True)
class RegionFinding:
    """One scored region — a tamper region with its own advisory tier."""

    region: TamperRegion
    tier: ConfidenceTier
    reason: str


@dataclass(frozen=True)
class ImageForensicsReport:
    """Rich Stage-6 result carried as the ``StageResult.payload`` (§9 manifest too).

    ``ok`` is RUN success (False only on an internal failure), independent of the
    verdict. ``findings`` are the per-region scored findings; the headline
    ``tier`` / ``score`` are the worst-case rollup.
    """

    tier: ConfidenceTier
    score: int | None
    findings: tuple[RegionFinding, ...] = ()
    summary: str = ""
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    detection: DetectionResult | None = field(default=None, repr=False)
    ok: bool = True
    error: str | None = None


# --------------------------------------------------------------------------- #
# Rule tree
# --------------------------------------------------------------------------- #

def score(
    detection: DetectionResult,
    activation: DocumentActivation,
    config: ImageForensicsConfig | None = None,
) -> ImageForensicsReport:
    """Apply the §7 rule tree to a detection result (never raises)."""
    cfg = config or ImageForensicsConfig()

    # ---- INCONCLUSIVE: no image-dominant page (digital-native). ----------- #
    if not activation.image_dominant_pages:
        return ImageForensicsReport(
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            summary="image_forensics: inconclusive (no image-dominant page)",
            reasons=("no image-dominant page — digital-native document, defers to Stages 1-4",),
        )

    # ---- INCONCLUSIVE: dominant pages, but nothing actually analysed. ----- #
    # Only capability gaps (deferred DSP) — never a verdict from no signal.
    if not detection.analyzed:
        notes = _gap_notes(detection)
        return ImageForensicsReport(
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            summary="image_forensics: inconclusive (no forensic method available)",
            reasons=(
                "image-dominant page(s) present, but no forensic method was able to "
                "analyse them (classical pixel math deferred) — no signal",
            ),
            notes=notes,
            detection=detection,
        )

    co_located = [r for r in detection.regions if r.co_located]
    high_local = [r for r in co_located if _is_local(r, cfg)]
    band_like = [r for r in co_located if not _is_local(r, cfg)]
    lone = [r for r in detection.regions if not r.co_located]
    diffuse_lone, local_lone = _partition_diffuse_lone(lone, cfg)
    strong_lone = [
        r for r in local_lone if r.strength >= cfg.region_medium_min_strength
    ]
    weak_lone = [
        r for r in local_lone if r.strength < cfg.region_medium_min_strength
    ]

    # ---- HIGH: two independent co-located methods over the same region. --- #
    if high_local:
        return _high(high_local, band_like, local_lone, diffuse_lone, detection, cfg)

    # A corroborated but page-spanning band remains reviewable evidence, but it
    # is not a localized pixel edit and therefore cannot originate HIGH.
    if band_like:
        return _medium_nonlocal(band_like, local_lone, diffuse_lone, detection, cfg)

    # ---- MEDIUM: a lone suspicious region, or a method that errored. ------ #
    if strong_lone:
        return _medium_region(strong_lone, weak_lone, diffuse_lone, detection, cfg)
    if detection.method_errors:
        return _medium_error(weak_lone, diffuse_lone, detection, cfg)

    # ---- LOW: analysed, only weak / diffuse / no localised signal. -------- #
    return _low(weak_lone, diffuse_lone, detection, cfg)


# --------------------------------------------------------------------------- #
# Tier builders
# --------------------------------------------------------------------------- #

def _high(
    co_located: list[TamperRegion],
    band_like: list[TamperRegion],
    lone: list[TamperRegion],
    diffuse_lone: list[TamperRegion],
    detection: DetectionResult,
    cfg: ImageForensicsConfig,
) -> ImageForensicsReport:
    high_value = any(r.high_value for r in co_located)
    score = min(100, cfg.score_high + (cfg.score_high_value_bump if high_value else 0))
    findings: list[RegionFinding] = []
    for r in co_located:
        methods = " + ".join(r.methods)
        hv = " over the high-value (amount) band" if r.high_value else ""
        findings.append(
            RegionFinding(
                region=r,
                tier=ConfidenceTier.HIGH,
                reason=(
                    f"two independent forensic signals ({methods}) are co-located"
                    f"{hv} — strong evidence of a localized pixel edit"
                ),
            )
        )
    findings.extend(_nonlocal_findings(band_like))
    # Lone regions alongside a HIGH are still reported (as their own lower tier).
    findings.extend(_lone_findings(lone, cfg))
    findings.extend(_diffuse_findings(diffuse_lone))
    reasons = [
        f"{len(co_located)} co-located multi-method region(s) — the §7 HIGH gate "
        "(a single method never reaches HIGH alone)"
    ]
    if high_value:
        reasons.append("a co-located region overlaps the high-value (amount) band")
    if band_like:
        reasons.append(
            f"{len(band_like)} page-spanning co-located region(s) capped at MEDIUM"
        )
    return ImageForensicsReport(
        tier=ConfidenceTier.HIGH,
        score=score,
        findings=tuple(findings),
        summary=f"image_forensics: HIGH (score {score}); {len(findings)} finding(s)",
        reasons=tuple(reasons),
        notes=_gap_notes(detection),
        detection=detection,
    )


def _medium_nonlocal(
    band_like: list[TamperRegion],
    lone: list[TamperRegion],
    diffuse_lone: list[TamperRegion],
    detection: DetectionResult,
    cfg: ImageForensicsConfig,
) -> ImageForensicsReport:
    findings = _nonlocal_findings(band_like)
    findings += _lone_findings(lone, cfg)
    findings += _diffuse_findings(diffuse_lone)
    reasons = [
        f"{len(band_like)} co-located multi-method region(s) are page-spanning "
        "rather than localized — capped at MEDIUM for review"
    ]
    if detection.method_errors:
        reasons.append(
            f"{len(detection.method_errors)} forensic method(s) errored on an "
            "image-dominant page (not silently dropped)"
        )
    return ImageForensicsReport(
        tier=ConfidenceTier.MEDIUM,
        score=cfg.score_medium,
        findings=tuple(findings),
        summary=(
            f"image_forensics: MEDIUM (score {cfg.score_medium}); "
            f"{len(findings)} finding(s)"
        ),
        reasons=tuple(reasons),
        notes=_gap_notes(detection),
        detection=detection,
    )


def _medium_region(
    strong_lone: list[TamperRegion],
    weak_lone: list[TamperRegion],
    diffuse_lone: list[TamperRegion],
    detection: DetectionResult,
    cfg: ImageForensicsConfig,
) -> ImageForensicsReport:
    findings = _lone_findings(strong_lone, cfg, force=ConfidenceTier.MEDIUM)
    findings += _lone_findings(weak_lone, cfg)
    findings += _diffuse_findings(diffuse_lone)
    reasons = [
        f"{len(strong_lone)} localized region(s) from a single method, "
        "uncorroborated — capped at MEDIUM (cross-stage corroboration may lift it)"
    ]
    if detection.method_errors:
        reasons.append(
            f"{len(detection.method_errors)} forensic method(s) errored on an "
            "image-dominant page (not silently dropped)"
        )
    return ImageForensicsReport(
        tier=ConfidenceTier.MEDIUM,
        score=cfg.score_medium,
        findings=tuple(findings),
        summary=f"image_forensics: MEDIUM (score {cfg.score_medium}); {len(findings)} finding(s)",
        reasons=tuple(reasons),
        notes=_gap_notes(detection),
        detection=detection,
    )


def _medium_error(
    weak_lone: list[TamperRegion],
    diffuse_lone: list[TamperRegion],
    detection: DetectionResult,
    cfg: ImageForensicsConfig,
) -> ImageForensicsReport:
    findings = _lone_findings(weak_lone, cfg)
    findings += _diffuse_findings(diffuse_lone)
    n = len(detection.method_errors)
    return ImageForensicsReport(
        tier=ConfidenceTier.MEDIUM,
        score=cfg.score_medium_method_error,
        findings=tuple(findings),
        summary=(
            f"image_forensics: MEDIUM (score {cfg.score_medium_method_error}); "
            f"{n} method error(s)"
        ),
        reasons=(
            f"{n} forensic method(s) errored on an image-dominant page — never "
            "silently dropped; a reviewer should confirm",
        ),
        notes=_gap_notes(detection),
        detection=detection,
    )


def _low(
    weak_lone: list[TamperRegion],
    diffuse_lone: list[TamperRegion],
    detection: DetectionResult,
    cfg: ImageForensicsConfig,
) -> ImageForensicsReport:
    findings = _lone_findings(weak_lone, cfg)
    findings += _diffuse_findings(diffuse_lone)
    if diffuse_lone:
        methods = sorted({r.methods[0] if r.methods else "unknown" for r in diffuse_lone})
        reason = (
            f"{len(diffuse_lone)} scattered lone region(s) from "
            f"{', '.join(methods)} form a diffuse, uncorroborated signal — reported "
            "at LOW, consistent with document structure / scanner noise"
        )
    elif detection.global_signals and not weak_lone:
        reason = (
            "only a global / whole-image signal (diffuse recompression or uniform "
            "scanner artifact) — consistent with an innocent rescan, not a local edit"
        )
    elif weak_lone:
        reason = (
            f"{len(weak_lone)} weak isolated blob(s) below the MEDIUM strength floor "
            "— not corroborated, treated as benign noise"
        )
    else:
        reason = "image-dominant page(s) analysed; no forensic method fired above its noise floor"
    return ImageForensicsReport(
        tier=ConfidenceTier.LOW,
        score=cfg.score_low,
        findings=tuple(findings),
        summary=f"image_forensics: LOW (score {cfg.score_low})",
        reasons=(reason,),
        notes=_gap_notes(detection),
        detection=detection,
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _lone_findings(
    regions: list[TamperRegion],
    cfg: ImageForensicsConfig,
    *,
    force: ConfidenceTier | None = None,
) -> list[RegionFinding]:
    out: list[RegionFinding] = []
    for r in regions:
        tier = force or (
            ConfidenceTier.MEDIUM
            if r.strength >= cfg.region_medium_min_strength
            else ConfidenceTier.LOW
        )
        method = r.methods[0] if r.methods else "unknown"
        out.append(
            RegionFinding(
                region=r,
                tier=tier,
                reason=(
                    f"single-method region ({method}), uncorroborated"
                    + (" — over the high-value (amount) band" if r.high_value else "")
                ),
            )
        )
    return out


def _diffuse_findings(regions: list[TamperRegion]) -> list[RegionFinding]:
    """Report diffuse single-method regions without promoting their peak strength."""
    return [
        RegionFinding(
            region=r,
            tier=ConfidenceTier.LOW,
            reason=(
                f"single-method regions ({r.methods[0] if r.methods else 'unknown'}) "
                "are diffuse across the page — uncorroborated scanner/document "
                "structure, not localized edit evidence"
            ),
        )
        for r in regions
    ]


def _partition_diffuse_lone(
    regions: list[TamperRegion], cfg: ImageForensicsConfig
) -> tuple[list[TamperRegion], list[TamperRegion]]:
    """Split lone regions into diffuse LOW evidence and localized candidates.

    A method becomes diffuse when it produces too many uncorroborated regions in
    the document. Independently, only the regions on a page whose aggregate bbox
    coverage crosses the configured page fraction are demoted. Co-located regions
    never enter this helper, so corroborated HIGH/MEDIUM paths remain untouched.
    """
    by_method: dict[str, list[int]] = {}
    by_method_page: dict[tuple[str, int], list[int]] = {}
    for index, region in enumerate(regions):
        method = region.methods[0] if region.methods else "unknown"
        by_method.setdefault(method, []).append(index)
        by_method_page.setdefault((method, region.page_index), []).append(index)

    diffuse_indices: set[int] = set()
    for indices in by_method.values():
        if len(indices) >= cfg.diffuse_lone_min_count:
            diffuse_indices.update(indices)
    for indices in by_method_page.values():
        coverage = sum(_page_area_fraction(regions[index]) for index in indices)
        if coverage >= cfg.diffuse_lone_coverage_frac:
            diffuse_indices.update(indices)

    diffuse = [r for index, r in enumerate(regions) if index in diffuse_indices]
    local = [r for index, r in enumerate(regions) if index not in diffuse_indices]
    return diffuse, local


def _page_area_fraction(region: TamperRegion) -> float:
    """Return a region's normalized page area, or zero without usable geometry."""
    if region.page_bbox is None or not region.page_width_pt or not region.page_height_pt:
        return 0.0
    x0, top, x1, bottom = region.page_bbox
    width = max(0.0, x1 - x0)
    height = max(0.0, bottom - top)
    return (width * height) / (region.page_width_pt * region.page_height_pt)


def _nonlocal_findings(regions: list[TamperRegion]) -> list[RegionFinding]:
    """Report corroborated page-spanning regions without calling them splices."""
    return [
        RegionFinding(
            region=r,
            tier=ConfidenceTier.MEDIUM,
            reason=(
                f"co-located forensic signals ({' + '.join(r.methods)}) span too "
                "much of the page to establish a localized pixel edit — review"
            ),
        )
        for r in regions
    ]


def _is_local(r: TamperRegion, cfg: ImageForensicsConfig) -> bool:
    """Whether a region has enough page geometry to originate localized HIGH."""
    if r.page_bbox is None or not r.page_width_pt or not r.page_height_pt:
        # Missing geometry must not silently suppress genuine evidence.
        return True
    x0, top, x1, bottom = r.page_bbox
    width = max(0.0, x1 - x0)
    height = max(0.0, bottom - top)
    width_frac = width / r.page_width_pt
    area_frac = (width * height) / (r.page_width_pt * r.page_height_pt)
    return (
        width_frac <= cfg.splice_max_width_frac
        and area_frac <= cfg.splice_max_area_frac
    )


def _gap_notes(detection: DetectionResult | None) -> tuple[str, ...]:
    if detection is None or not detection.capability_gaps:
        return ()
    methods = sorted({g.method for g in detection.capability_gaps})
    return (
        f"forensic method(s) not yet available (deferred classical DSP): "
        f"{', '.join(methods)}",
    )


__all__ = ["RegionFinding", "ImageForensicsReport", "score"]
