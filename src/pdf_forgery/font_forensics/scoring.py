"""Aggregate font findings into a confidence tier + score.

Mirrors the revision-recovery convention: an explicit rule tree, not a weighted
sum, with every score value sourced from :class:`FontConfig`.

    INCONCLUSIVE — fewer than two comparable glyphs, or a single uniform font
                   across the whole document (nothing to compare).
    LOW          — multiple fonts present but only benign styling variation
                   (no findings raised).
    MEDIUM       — an intra-line subset switch off a high-value token, or a
                   high-value baseline deviation with no line context.
    HIGH         — a confident high-value token contains an internal mixed-glyph
                   font seam. Whole-token differences are capped below HIGH.
"""

from __future__ import annotations

from .config import FontConfig
from .models import (
    ClassificationStrength,
    ConfidenceTier,
    FontFinding,
    FontFindingKind,
    HighValueKind,
)


def score_findings(
    findings: list[FontFinding],
    distinct_font_count: int,
    comparable_glyphs: int,
    config: FontConfig | None = None,
) -> tuple[ConfidenceTier, int | None, list[str]]:
    """Return ``(tier, score, reasons)`` for a set of font findings."""
    cfg = config or FontConfig()

    if comparable_glyphs < cfg.min_glyphs_for_analysis or distinct_font_count <= 1:
        return (
            ConfidenceTier.INCONCLUSIVE,
            None,
            ["single uniform font — nothing to compare (route to later stages)"],
        )

    high = [
        f
        for f in findings
        if f.tier is ConfidenceTier.HIGH
        and f.kind is FontFindingKind.INTRA_TOKEN_FONT_MIX
        and f.classification_strength
        in (ClassificationStrength.STRONG, ClassificationStrength.MEDIUM)
    ]
    medium = [f for f in findings if f.tier is ConfidenceTier.MEDIUM]

    if high:
        score = _high_score(high, cfg)
        reasons = [
            "high-value token contains a glyph-level font/subset seam"
        ]
        if any(_is_amount_or_date(f) for f in high):
            reasons.append("high-value field altered (amount/date font mismatch)")
        return (ConfidenceTier.HIGH, score, reasons)

    if medium:
        score = max(_medium_score(f, cfg) for f in medium)
        return (
            ConfidenceTier.MEDIUM,
            score,
            ["font inconsistency requires review but lacks independent HIGH evidence"],
        )

    low = [f for f in findings if f.tier is ConfidenceTier.LOW]
    if low:
        score = max(
            cfg.score_low_document_fallback
            if f.kind is FontFindingKind.DOCUMENT_BASELINE_DEVIATION
            else cfg.score_low_default
            for f in low
        )
        return (
            ConfidenceTier.LOW,
            score,
            ["weak whole-token or global-baseline font evidence only"],
        )

    # Multiple fonts, nothing suspicious: ordinary styling.
    return (
        ConfidenceTier.LOW,
        cfg.score_low_default,
        ["multiple fonts present but only benign styling variation found"],
    )


def _is_amount_or_date(f: FontFinding) -> bool:
    return f.high_value in (HighValueKind.AMOUNT, HighValueKind.DATE)


def _high_score(high: list[FontFinding], cfg: FontConfig) -> int:
    if any(_is_amount_or_date(f) for f in high):
        return cfg.score_high_amount_date
    return cfg.score_high_id_like


def _medium_score(f: FontFinding, cfg: FontConfig) -> int:
    if f.kind is FontFindingKind.PAGE_BASELINE_DEVIATION:
        return cfg.score_medium_baseline_deviation
    if f.kind is FontFindingKind.INTRA_TOKEN_FONT_MIX:
        return cfg.score_medium_intra_token_mix
    if f.kind in (
        FontFindingKind.WHOLE_TOKEN_SUBSET_DIFFERENCE,
        FontFindingKind.WHOLE_TOKEN_FAMILY_DIFFERENCE,
    ):
        return cfg.score_medium_whole_token_difference
    return cfg.score_medium_subset_split
