"""Fuse per-stage :class:`StageResult` objects into ONE overall assessment.

The pipeline runs each stage independently and collects a list of advisory
:class:`~pdf_forgery.core.types.StageResult`. A reviewer still wants a single
headline: is the overall confidence LOW, MEDIUM, or HIGH?

Fusion is NOT a vote count and NOT an average. It is evidence-weighted
escalation built on two ideas:

1. **Stage roles.** Some stages are *substantive* — they can independently
   indicate a content edit (revision_recovery, font_forensics,
   invoice_arithmetic). Others are *corroborators* — they strengthen a case but
   must never originate a verdict (provenance_metadata: a re-render footprint is
   suspicious only alongside real evidence of an edit).

2. **Corroboration lifts; it does not originate.** The overall floor is the
   strongest substantive tier (``INCONCLUSIVE`` = "this method couldn't assess",
   i.e. no signal — it never drags the verdict down). A substantive MEDIUM is
   escalated to HIGH when an *independent* stage corroborates it — either a
   second substantive stage at >= MEDIUM, or the corroborator firing. A lone
   substantive MEDIUM with nothing to back it stays MEDIUM; corroboration over an
   all-clean substantive picture (or a corroborator firing by itself) stays LOW.

This realises the rule the per-stage gates defer to: e.g. invoice_arithmetic
caps a lone gross break at MEDIUM precisely because cross-stage corroboration is
fusion's job — a broken amount (MEDIUM) plus a consumer-re-render footprint
(provenance) together are HIGH.

This module is READ-ONLY over the stage results; it changes no per-stage score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .core.types import ConfidenceTier, StageResult

# Default role assignment by stage name. Anything not listed is treated as
# substantive (a new edit-evidence stage should count, not be silently ignored).
DEFAULT_CORROBORATOR_STAGES = frozenset({"provenance_metadata"})

# Severity for comparing the three "signal" tiers. INCONCLUSIVE is deliberately
# absent — it means "no signal", handled separately, never a severity of its own.
_SEVERITY = {
    ConfidenceTier.LOW: 1,
    ConfidenceTier.MEDIUM: 2,
    ConfidenceTier.HIGH: 3,
}


@dataclass
class FusionConfig:
    """Tunable parameters for fusing stage results."""

    corroborator_stages: frozenset[str] = DEFAULT_CORROBORATOR_STAGES
    """Stage names that may only corroborate, never originate, a verdict."""

    escalation_bonus: int = 20
    """Score added to the strongest substantive MEDIUM when corroboration lifts
    it to HIGH (then clamped into the HIGH band)."""

    high_band_floor: int = 70
    """Lowest score an escalated HIGH may take."""

    score_max: int = 100

    default_low_score: int = 15
    """Fallback LOW score when no contributing stage supplies one."""


@dataclass(frozen=True)
class FusedAssessment:
    """The single overall verdict fused from all stage results (advisory)."""

    tier: ConfidenceTier
    score: int | None
    reasons: tuple[str, ...] = ()
    """Ordered explanation of how the overall tier was reached."""

    contributing_stages: tuple[str, ...] = ()
    """Stages whose signal drove the verdict (floor + any corroborators)."""

    stage_tiers: tuple[tuple[str, str], ...] = ()
    """``(stage_name, tier)`` for every stage considered, for transparency."""

    notes: tuple[str, ...] = ()
    """Diagnostics (e.g. stages that failed to run)."""


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def fuse(
    results: Sequence[StageResult], config: FusionConfig | None = None
) -> FusedAssessment:
    """Combine stage results into one :class:`FusedAssessment` (never raises)."""
    cfg = config or FusionConfig()

    stage_tiers = tuple((r.stage, r.tier.value) for r in results)
    notes = tuple(
        f"{r.stage}: did not run ({r.error or 'unknown error'})"
        for r in results
        if not r.ok
    )

    ok = [r for r in results if r.ok]
    if not ok:
        return FusedAssessment(
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            reasons=("no stage produced a usable result",),
            stage_tiers=stage_tiers,
            notes=notes,
        )

    substantive = [r for r in ok if r.stage not in cfg.corroborator_stages]
    corroborators = [r for r in ok if r.stage in cfg.corroborator_stages]

    sub_signals = [r for r in substantive if r.tier in _SEVERITY]  # exclude INCONCLUSIVE
    high_sub = [r for r in sub_signals if r.tier is ConfidenceTier.HIGH]
    medium_sub = [r for r in sub_signals if r.tier is ConfidenceTier.MEDIUM]
    low_sub = [r for r in sub_signals if r.tier is ConfidenceTier.LOW]
    corrob_fires = [r for r in corroborators if r.tier in (ConfidenceTier.MEDIUM, ConfidenceTier.HIGH)]

    # ---- 1. A substantive stage already at HIGH -> HIGH. ------------------ #
    if high_sub:
        score = _max_score(high_sub, cfg.high_band_floor)
        contributing = tuple(r.stage for r in high_sub) + tuple(r.stage for r in corrob_fires)
        reasons = [
            f"{_join_stages(high_sub)} report HIGH on their own — strong evidence "
            "of an edit"
        ]
        if corrob_fires:
            reasons.append(f"corroborated by {_join_stages(corrob_fires)}")
        return _assess(ConfidenceTier.HIGH, score, reasons, contributing, stage_tiers, notes)

    # ---- 2. Substantive MEDIUM: escalate to HIGH only with corroboration. -- #
    if medium_sub:
        second_substantive = len(medium_sub) >= 2
        corroborated = second_substantive or bool(corrob_fires)
        base = _max_score(medium_sub, 30)
        if corroborated:
            score = min(cfg.score_max, max(cfg.high_band_floor, base + cfg.escalation_bonus))
            contributing = tuple(r.stage for r in medium_sub) + tuple(r.stage for r in corrob_fires)
            if second_substantive:
                why = (
                    f"two independent substantive stages ({_join_stages(medium_sub)}) "
                    "both report MEDIUM"
                )
            else:
                why = (
                    f"{_join_stages(medium_sub)} reports a localized MEDIUM finding, "
                    f"independently corroborated by {_join_stages(corrob_fires)}"
                )
            reasons = [
                f"escalated MEDIUM -> HIGH: {why} — corroborating signals compound",
            ]
            return _assess(ConfidenceTier.HIGH, score, reasons, contributing, stage_tiers, notes)

        # Lone substantive MEDIUM, nothing to back it -> stays MEDIUM.
        contributing = tuple(r.stage for r in medium_sub)
        reasons = [
            f"{_join_stages(medium_sub)} reports MEDIUM, but no independent stage "
            "corroborates it — a reviewer should confirm (could be source-data error)"
        ]
        return _assess(ConfidenceTier.MEDIUM, base, reasons, contributing, stage_tiers, notes)

    # ---- 3. Only substantive LOW (corroboration cannot lift LOW). --------- #
    if low_sub:
        contributing = tuple(r.stage for r in low_sub)
        reasons = [
            f"{_join_stages(low_sub)} found only benign signals; substantive "
            "evidence of an edit is absent"
        ]
        if corrob_fires:
            reasons.append(
                f"{_join_stages(corrob_fires)} fired, but a corroborator cannot "
                "originate a verdict — treated as clean"
            )
        return _assess(
            ConfidenceTier.LOW, _max_score(low_sub, cfg.default_low_score),
            reasons, contributing, stage_tiers, notes,
        )

    # ---- 4. No substantive signal at all (all substantive INCONCLUSIVE). -- #
    if corrob_fires:
        # Provenance alone -> overall clean (corroborator never originates).
        reasons = [
            f"only {_join_stages(corrob_fires)} fired (a corroborator); no "
            "substantive stage found evidence of an edit — treated as clean"
        ]
        return _assess(
            ConfidenceTier.LOW, cfg.default_low_score, reasons,
            tuple(r.stage for r in corrob_fires), stage_tiers, notes,
        )

    # No substantive stage applied, but a stage still read the file and
    # AFFIRMATIVELY cleared it (e.g. provenance LOW = no metadata anomalies).
    # That is a successful analysis with no positive signal anywhere -> the
    # file is clean, not "couldn't assess". INCONCLUSIVE is reserved for files
    # no method could actually read (encrypted/corrupt/empty), where nothing
    # affirmatively clears them. A corroborator may clear toward clean even
    # though it can never ORIGINATE suspicion.
    cleared = [r for r in ok if r.tier is ConfidenceTier.LOW]
    if cleared:
        reasons = [
            "no detector found evidence of an edit, and the file was analyzed "
            f"successfully — {_join_stages(cleared)} affirmatively cleared it; "
            "the other methods do not apply to this document — treated as clean"
        ]
        return _assess(
            ConfidenceTier.LOW, _max_score(cleared, cfg.default_low_score),
            reasons, tuple(r.stage for r in cleared), stage_tiers, notes,
        )

    # Every stage was INCONCLUSIVE and nothing cleared the file -> truly
    # unassessable by this set of methods (e.g. encrypted/corrupt).
    reasons = ["every stage was inconclusive — this set of methods could not assess the file"]
    return FusedAssessment(
        tier=ConfidenceTier.INCONCLUSIVE,
        score=None,
        reasons=tuple(reasons),
        contributing_stages=(),
        stage_tiers=stage_tiers,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_overall_summary(assessment: FusedAssessment) -> str:
    """Human-readable overall verdict; confidence is ADVISORY."""
    lines: list[str] = []
    score_txt = "n/a" if assessment.score is None else str(assessment.score)
    lines.append(
        f"OVERALL ASSESSMENT: {assessment.tier.value.upper()} (score {score_txt}) — ADVISORY"
    )
    if assessment.stage_tiers:
        per_stage = ", ".join(f"{name}={tier.upper()}" for name, tier in assessment.stage_tiers)
        lines.append(f"  Per stage: {per_stage}")
    if assessment.contributing_stages:
        lines.append(f"  Driven by: {', '.join(assessment.contributing_stages)}")
    for r in assessment.reasons:
        lines.append(f"    - {r}")
    for n in assessment.notes:
        lines.append(f"    (note) {n}")
    lines.append("  (Overall confidence is advisory; a human reviewer makes the final call.)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _max_score(results: list[StageResult], fallback: int) -> int:
    scores = [r.score for r in results if r.score is not None]
    return max(scores) if scores else fallback


def _join_stages(results: list[StageResult]) -> str:
    return " + ".join(r.stage for r in results)


def _assess(
    tier: ConfidenceTier,
    score: int,
    reasons: list[str],
    contributing: tuple[str, ...],
    stage_tiers: tuple[tuple[str, str], ...],
    notes: tuple[str, ...],
) -> FusedAssessment:
    return FusedAssessment(
        tier=tier,
        score=score,
        reasons=tuple(reasons),
        contributing_stages=contributing,
        stage_tiers=stage_tiers,
        notes=notes,
    )
