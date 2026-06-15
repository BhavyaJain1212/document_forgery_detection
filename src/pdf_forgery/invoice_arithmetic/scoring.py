"""Aggregate arithmetic findings into a confidence tier + score.

An explicit rule tree (not a weighted sum), with every score value sourced from
:class:`InvoiceConfig`. Convergence-gated per the owner decision: a lone gross
broken equation is capped at strong MEDIUM; HIGH requires that one cell's
correction reconciles multiple independent equations (convergence), the only
self-contained corroboration available from the submitted bill alone.

    INCONCLUSIVE — no table/roles identified, or too few numeric cells /
                   evaluable relationships (prefer this over guessing).
    LOW          — every evaluated relationship reconciles within tolerance.
    MEDIUM       — >= 1 broken relationship but no convergence (lone break):
                   strong MEDIUM when gross + high-value, lower otherwise.
    HIGH         — a broken relationship whose localized cell converges (its
                   correction reconciles >= convergence_high_threshold equations).
"""

from __future__ import annotations

from .config import InvoiceConfig
from .models import (
    ArithmeticFinding,
    ConfidenceTier,
    HighValueKind,
    Relationship,
)


def score(
    findings: list[ArithmeticFinding],
    relationships: list[Relationship],
    numeric_cell_count: int,
    table_found: bool,
    config: InvoiceConfig | None = None,
) -> tuple[ConfidenceTier, int | None, list[str]]:
    """Return ``(tier, score, reasons)`` for a set of arithmetic findings."""
    cfg = config or InvoiceConfig()

    if (
        not table_found
        or numeric_cell_count < cfg.min_numeric_cells
        or len(relationships) < cfg.min_relationships
    ):
        return (
            ConfidenceTier.INCONCLUSIVE,
            None,
            [
                "table structure / column roles could not be identified reliably, "
                "or too few numeric relationships to evaluate (route to later stages)"
            ],
        )

    if not findings:
        n = len(relationships)
        return (
            ConfidenceTier.LOW,
            cfg.score_low_reconciled,
            [f"all {n} evaluated relationship(s) reconcile within tolerance"],
        )

    high = [f for f in findings if f.tier is ConfidenceTier.HIGH]
    if high:
        score_val = _high_score(high, cfg)
        top = max(high, key=lambda f: f.convergence_count)
        reasons = [
            f"{len(high)} relationship(s) broken with convergence — a single "
            f"cell's correction reconciles {top.convergence_count} equations "
            "(strong evidence of an edited value)",
        ]
        if any(_is_high_value(f) for f in high):
            reasons.append("high-value field altered (amount/date)")
        return (ConfidenceTier.HIGH, score_val, reasons)

    # MEDIUM: lone broken equation(s), no convergence.
    gross = [f for f in findings if f.is_gross]
    if gross:
        score_val = cfg.score_medium_lone_gross
        reasons = [
            f"{len(gross)} gross broken relationship(s) localized to a cell, but "
            "no second equation corroborates the edit — strong MEDIUM (a human "
            "reviewer / later stage should confirm; could also be source-data error)"
        ]
    else:
        score_val = cfg.score_medium_lone_minor
        reasons = [
            f"{len(findings)} relationship(s) just outside tolerance — could be "
            "rounding or extraction noise; review"
        ]
    return (ConfidenceTier.MEDIUM, score_val, reasons)


def _is_high_value(f: ArithmeticFinding) -> bool:
    return f.high_value in (HighValueKind.AMOUNT, HighValueKind.DATE)


def _high_score(high: list[ArithmeticFinding], cfg: InvoiceConfig) -> int:
    if any(_is_high_value(f) for f in high):
        return cfg.score_high_amount_convergence
    return cfg.score_high_convergence
