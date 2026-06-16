"""Scoring — weighted divergence → tier/score rule tree (CPU queue).

Aggregates the surviving divergences into one token-weighted score and maps it
to the locked ``core.ConfidenceTier`` schema via an EXPLICIT rule tree (not a
black-box sum), consistent with the other stages. A high-value MISMATCH
(amount/date) can reach 90-100. See ``docs/STAGE3_DESIGN.md`` §6.
"""

from __future__ import annotations

from collections import defaultdict

from ..core.types import ConfidenceTier
from .config import OCRCrossCheckConfig
from .models import Divergence, DivergenceType, Stage3Result, TokenClass

# ID is excluded from both HIGH paths: Stage 1's own classifier already flags
# ID_LIKE (any 6+-char alphanumeric run) as a WEAK/noisy booster, and on real
# invoice prose it matches almost every ordinary word. An ID MISMATCH/orphan
# still contributes its weight to the divergence mass (MEDIUM/LOW) — it just
# never originates HIGH on its own. See docs/STAGE3_OCR_FALSE_POSITIVE_FIX.md.
_HIGH_VALUE = frozenset({TokenClass.AMOUNT, TokenClass.DATE})


def _orphan_text(d: Divergence) -> str:
    """Raw text of an EMBEDDED_ONLY/OCR_ONLY divergence's lone word."""
    if d.embedded:
        return d.embedded[0].text
    if d.ocr is not None:
        return d.ocr.text
    return ""


def _divergence_mass(
    divergences: list[Divergence], config: OCRCrossCheckConfig
) -> float:
    """Sum of non-AGREE weights, with repeated identical orphans capped (RC#2 fix F).

    A single orphan text repeated once per page (a logo/header/footer the OCR
    engine sees but the embedded layer doesn't, or vice versa) is one
    systematic extraction artifact — its contribution to mass is capped at
    ``repeated_orphan_cap`` instances rather than scaling with page count. Real
    MISMATCHes are never capped.
    """
    from .normalize import fold

    mass = 0.0
    orphan_groups: dict[tuple[DivergenceType, str], list[Divergence]] = defaultdict(list)

    for d in divergences:
        if d.type is DivergenceType.AGREE:
            continue
        if d.type in (DivergenceType.EMBEDDED_ONLY, DivergenceType.OCR_ONLY):
            key = (d.type, fold(_orphan_text(d), config))
            orphan_groups[key].append(d)
        else:
            mass += d.weight

    for group in orphan_groups.values():
        mass += sum(d.weight for d in group[: config.repeated_orphan_cap])

    return mass


def score(
    divergences: list[Divergence],
    *,
    routed_to: str | None = None,
    config: OCRCrossCheckConfig | None = None,
    compared_words: int = 0,
) -> Stage3Result:
    """Map aggregated, token-weighted divergence to a :class:`Stage3Result` (§6).

    Rule tree (checked in priority order):

    INCONCLUSIVE — ``routed_to`` set (scanned route) OR no divergences at all
        (nothing was compared — zero matched groups and zero unmatched).
        ``score=None``.

    HIGH (70-100) — at least one AMOUNT/DATE MISMATCH, or a CORROBORATED
        AMOUNT/DATE orphan cluster:
        * AMOUNT/DATE MISMATCH  → ``score_high_amount_date_mismatch`` (95)
        * AMOUNT/DATE orphan (EMBEDDED_ONLY or OCR_ONLY), corroborated by
          either ``>= min_high_value_orphans`` of them or an accompanying
          high-value MISMATCH → ``score_high_value_orphan`` (75)
        Multiple triggers escalate to the highest applicable score. ID never
        originates HIGH (see module note).

    MEDIUM (30-70) — a single, UNCORROBORATED high-value orphan (the expected
        steady-state OCR noise on a clean page — score_medium_default), OR
        total divergence mass (sum of non-AGREE weights, including ID, with
        repeated identical orphans capped at ``repeated_orphan_cap`` — RC#2 fix
        F) is at or above ``max(medium_divergence_mass, divergence_mass_ratio *
        compared_words)`` — an absolute floor AND a floor relative to the
        volume of text actually compared, so a long document's steady-state
        per-page noise (e.g. a repeated header) doesn't cross an absolute mass
        floor just from page count (RC#2 fix E;
        see docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md). ``compared_words``
        defaults to 0, so callers that don't pass it keep the absolute-floor-only
        behaviour.

    LOW (0-30) — any divergence exists (non-empty list with at least one
        non-AGREE entry) but mass is below the MEDIUM threshold.
        Score is ``score_low_default``.

    If the divergence list is entirely AGREE (zero mass), but was non-empty
    (words were compared and all matched), the result is LOW with score 0
    (clean, but we did compare something).
    """
    cfg = config or OCRCrossCheckConfig()

    # INCONCLUSIVE path 1: routed (scanned / text-sparse)
    if routed_to is not None:
        return Stage3Result(
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            divergences=tuple(divergences),
            routed_to=routed_to,
        )

    # INCONCLUSIVE path 2: nothing was compared at all
    if not divergences:
        return Stage3Result(
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            divergences=(),
            routed_to=None,
        )

    # ------------------------------------------------------------------ #
    # Scan for HIGH triggers                                              #
    # ------------------------------------------------------------------ #
    best_high_score: int | None = None
    high_value_mismatch_present = False
    high_value_orphans: list[Divergence] = []

    for d in divergences:
        if d.type is DivergenceType.MISMATCH:
            if d.token_class in _HIGH_VALUE:
                candidate = cfg.score_high_amount_date_mismatch
                high_value_mismatch_present = True
                best_high_score = (
                    candidate if best_high_score is None
                    else max(best_high_score, candidate)
                )

        elif d.type in (DivergenceType.EMBEDDED_ONLY, DivergenceType.OCR_ONLY):
            if d.token_class in _HIGH_VALUE:
                high_value_orphans.append(d)

    # Orphan→HIGH needs corroboration: a lone high-value orphan is the
    # expected steady-state OCR noise on a clean page (a detector split/merged
    # a box, or dropped a stray fragment at the margin) — not by itself proof
    # of an overlay/hidden-text anomaly.
    corroborated_orphans = bool(high_value_orphans) and (
        len(high_value_orphans) >= cfg.min_high_value_orphans
        or high_value_mismatch_present
    )
    if corroborated_orphans:
        candidate = cfg.score_high_value_orphan
        best_high_score = (
            candidate if best_high_score is None
            else max(best_high_score, candidate)
        )

    if best_high_score is not None:
        return Stage3Result(
            tier=ConfidenceTier.HIGH,
            score=best_high_score,
            divergences=tuple(divergences),
            routed_to=None,
        )

    # An uncorroborated lone high-value orphan still needs human review —
    # MEDIUM, not LOW, regardless of where the overall mass would land it.
    if high_value_orphans:
        return Stage3Result(
            tier=ConfidenceTier.MEDIUM,
            score=cfg.score_medium_default,
            divergences=tuple(divergences),
            routed_to=None,
        )

    # ------------------------------------------------------------------ #
    # Divergence mass for MEDIUM / LOW                                   #
    # ------------------------------------------------------------------ #
    mass = _divergence_mass(divergences, cfg)
    medium_threshold = max(
        cfg.medium_divergence_mass,
        cfg.divergence_mass_ratio * compared_words,
    )

    if mass >= medium_threshold:
        return Stage3Result(
            tier=ConfidenceTier.MEDIUM,
            score=cfg.score_medium_default,
            divergences=tuple(divergences),
            routed_to=None,
        )

    if mass > 0:
        return Stage3Result(
            tier=ConfidenceTier.LOW,
            score=cfg.score_low_default,
            divergences=tuple(divergences),
            routed_to=None,
        )

    # All divergences are AGREE — document looks clean under this method.
    return Stage3Result(
        tier=ConfidenceTier.LOW,
        score=0,
        divergences=tuple(divergences),
        routed_to=None,
    )


__all__ = ["score"]
