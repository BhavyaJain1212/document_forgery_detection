"""Aggregate font findings into a confidence tier + score.

Mirrors the revision-recovery convention: an explicit rule tree, not a weighted
sum, with every score value sourced from :class:`FontConfig`.

    INCONCLUSIVE — fewer than two comparable glyphs, or a single uniform font
                   across the whole document (nothing to compare).
    LOW          — multiple fonts present but only benign styling variation
                   (no findings raised).
    MEDIUM       — an intra-line subset switch off a high-value token, or a
                   high-value baseline deviation with no line context.
    HIGH         — a high-value token whose font/subset breaks its line context.
"""

from __future__ import annotations

from .config import FontConfig
from .models import ConfidenceTier, FontFinding, HighValueKind


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

    high = [f for f in findings if f.tier is ConfidenceTier.HIGH]
    medium = [f for f in findings if f.tier is ConfidenceTier.MEDIUM]

    if high:
        score = _high_score(high, cfg)
        reasons = [
            "high-value token rendered in a font/subset that breaks its line context"
        ]
        if any(_is_amount_or_date(f) for f in high):
            reasons.append("high-value field altered (amount/date font mismatch)")
        return (ConfidenceTier.HIGH, score, reasons)

    if medium:
        score = max(_medium_score(f, cfg) for f in medium)
        return (
            ConfidenceTier.MEDIUM,
            score,
            ["intra-line font/subset switch not tied to a high-value token"],
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
    from .models import FontFindingKind

    if f.kind is FontFindingKind.HIGH_VALUE_BASELINE_DEVIATION:
        return cfg.score_medium_baseline_deviation
    if f.kind is FontFindingKind.INTRA_TOKEN_FONT_MIX:
        return cfg.score_medium_intra_token_mix
    return cfg.score_medium_subset_split
