"""Aggregate provenance findings into a confidence tier + score.

Provenance is a CORROBORATOR: it NEVER independently reaches HIGH. It tops out at
MEDIUM (capped by ``medium_ceiling``) and exists to strengthen a case when
arithmetic / font / revision findings also fire. Fusion (a later session) treats
a provenance-MEDIUM with nothing else as overall-clean.

    INCONCLUSIVE — metadata could not be read at all.
    LOW          — no anomalies, or only weak lone signals (a bare version
                   Producer, a browser Creator, or a ModDate>CreationDate on a
                   document that is otherwise unremarkable).
    MEDIUM       — a web-editor / composite / XMP-mismatch / ID-mismatch signal
                   (capped at the MEDIUM ceiling).
"""

from __future__ import annotations

from .config import ProvenanceConfig
from .models import (
    ConfidenceTier,
    ProvenanceFinding,
    ProvenanceFindingKind,
)

_SCORE_BY_KIND = {
    ProvenanceFindingKind.COMPOSITE_EDIT_FOOTPRINT: "score_composite",
    ProvenanceFindingKind.WEB_EDITOR_PRODUCER: "score_web_editor",
    ProvenanceFindingKind.XMP_INFO_PRODUCER_MISMATCH: "score_xmp_info_mismatch",
    ProvenanceFindingKind.ID_HALVES_MISMATCH: "score_id_mismatch",
    ProvenanceFindingKind.VERSION_ONLY_PRODUCER: "score_version_producer",
    ProvenanceFindingKind.MODDATE_AFTER_CREATION: "score_date_anomaly",
    ProvenanceFindingKind.BROWSER_CREATOR: "score_browser_creator",
}


def score(
    findings: list[ProvenanceFinding],
    metadata_readable: bool,
    config: ProvenanceConfig | None = None,
) -> tuple[ConfidenceTier, int | None, list[str]]:
    """Return ``(tier, score, reasons)`` — never HIGH (corroborator only)."""
    cfg = config or ProvenanceConfig()

    if not metadata_readable:
        return (
            ConfidenceTier.INCONCLUSIVE,
            None,
            ["no readable document metadata (Info/XMP) to assess provenance"],
        )

    if not findings:
        return (
            ConfidenceTier.LOW,
            cfg.score_clean,
            ["no provenance anomalies in producer/creator/dates/ID"],
        )

    raw = max(getattr(cfg, _SCORE_BY_KIND[f.kind]) for f in findings)
    capped = min(raw, cfg.medium_ceiling)

    has_medium = any(f.tier is ConfidenceTier.MEDIUM for f in findings)
    if has_medium:
        tier = ConfidenceTier.MEDIUM
        reasons = [
            "provenance signals consistent with a non-institutional re-render — "
            "corroborating evidence only (never conclusive on its own)"
        ]
    else:
        # Only weak LOW-tier signals (version Producer / browser / date anomaly).
        tier = ConfidenceTier.LOW
        reasons = [
            "weak provenance signals only (bare version producer / browser export "
            "/ modified-after-creation) — note for the reviewer, not conclusive"
        ]
        capped = min(capped, cfg.medium_ceiling)

    reasons.extend(f.reason for f in findings)
    return (tier, capped, reasons)
