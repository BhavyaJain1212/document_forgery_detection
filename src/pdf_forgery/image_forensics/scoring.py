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
               signal consistent with an innocent rescan, or a single weak
               isolated blob below the MEDIUM strength floor, or nothing fired.

MEDIUM       — exactly one of: a lone (uncorroborated) region above the strength
               floor, OR a method ERRORED on an image-dominant page (never
               silently dropped). Capped — a lone signal never reaches HIGH here;
               cross-stage corroboration lifts it in fusion (§7 fusion role).

HIGH         — at least one region with TWO independent, co-located methods over
               the same area (e.g. a DQ ghost AND a co-located noise break). The
               only path to HIGH within this stage.
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
    lone = [r for r in detection.regions if not r.co_located]
    strong_lone = [r for r in lone if r.strength >= cfg.region_medium_min_strength]
    weak_lone = [r for r in lone if r.strength < cfg.region_medium_min_strength]

    # ---- HIGH: two independent co-located methods over the same region. --- #
    if co_located:
        return _high(co_located, lone, detection, cfg)

    # ---- MEDIUM: a lone suspicious region, or a method that errored. ------ #
    if strong_lone:
        return _medium_region(strong_lone, weak_lone, detection, cfg)
    if detection.method_errors:
        return _medium_error(weak_lone, detection, cfg)

    # ---- LOW: analysed, only weak / diffuse / no localised signal. -------- #
    return _low(weak_lone, detection, cfg)


# --------------------------------------------------------------------------- #
# Tier builders
# --------------------------------------------------------------------------- #

def _high(
    co_located: list[TamperRegion],
    lone: list[TamperRegion],
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
    # Lone regions alongside a HIGH are still reported (as their own lower tier).
    findings.extend(_lone_findings(lone, cfg))
    reasons = [
        f"{len(co_located)} co-located multi-method region(s) — the §7 HIGH gate "
        "(a single method never reaches HIGH alone)"
    ]
    if high_value:
        reasons.append("a co-located region overlaps the high-value (amount) band")
    return ImageForensicsReport(
        tier=ConfidenceTier.HIGH,
        score=score,
        findings=tuple(findings),
        summary=f"image_forensics: HIGH (score {score}); {len(findings)} finding(s)",
        reasons=tuple(reasons),
        notes=_gap_notes(detection),
        detection=detection,
    )


def _medium_region(
    strong_lone: list[TamperRegion],
    weak_lone: list[TamperRegion],
    detection: DetectionResult,
    cfg: ImageForensicsConfig,
) -> ImageForensicsReport:
    findings = _lone_findings(strong_lone, cfg, force=ConfidenceTier.MEDIUM)
    findings += _lone_findings(weak_lone, cfg)
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
    detection: DetectionResult,
    cfg: ImageForensicsConfig,
) -> ImageForensicsReport:
    findings = _lone_findings(weak_lone, cfg)
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
    detection: DetectionResult,
    cfg: ImageForensicsConfig,
) -> ImageForensicsReport:
    findings = _lone_findings(weak_lone, cfg)
    if detection.global_signals and not weak_lone:
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


def _gap_notes(detection: DetectionResult | None) -> tuple[str, ...]:
    if detection is None or not detection.capability_gaps:
        return ()
    methods = sorted({g.method for g in detection.capability_gaps})
    return (
        f"forensic method(s) not yet available (deferred classical DSP): "
        f"{', '.join(methods)}",
    )


__all__ = ["RegionFinding", "ImageForensicsReport", "score"]
