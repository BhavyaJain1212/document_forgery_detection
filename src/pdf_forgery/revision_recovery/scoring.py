"""Scoring rule tree for Stage 1 (revision recovery).

Combines :class:`TextChange` and :class:`ObjectDiff` results from all
consecutive revision pairs into a single :class:`ScoringResult` with a
:class:`ConfidenceTier` and numeric score.

The rule tree follows the spec verbatim (see CLAUDE.md §Confidence tiers):

    INCONCLUSIVE — only one revision detected (single-revision PDF).
    HIGH         — substantive text diff in a CONTENT object.
    MEDIUM       — any of: CONTENT changed but no text diff; OVERLAY changed;
                   FIELD_EDIT occurred; reconstruction failure.
    LOW          — multiple revisions, all changes benign
                   (SIGNATURE / META / MARKUP / FORM_FILL).

Priority: HIGH > MEDIUM > LOW.  INCONCLUSIVE is checked first.

Public entry point
------------------
score(text_changes, object_diffs, recon_result, config=None) -> ScoringResult
"""

from __future__ import annotations

from collections.abc import Sequence

from .config import Config
from .models import (
    ConfidenceTier,
    HighValueKind,
    ObjectChangeClass,
    ObjectDiff,
    ReconstructionResult,
    ScoringResult,
    TextChange,
    TokenDiff,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _best_hv_kind(text_changes: Sequence[TextChange]) -> HighValueKind | None:
    """Return the highest-priority HighValueKind across all token changes."""
    priority = {HighValueKind.AMOUNT: 0, HighValueKind.DATE: 1, HighValueKind.ID_LIKE: 2}
    best: HighValueKind | None = None
    for tc in text_changes:
        for pd in tc.page_diffs:
            for tok in pd.token_changes:
                if tok.high_value is not None:
                    if best is None or priority[tok.high_value] < priority[best]:
                        best = tok.high_value
    return best


def _effective_hv_kind(
    kind: HighValueKind | None,
    cfg: Config,
) -> HighValueKind | None:
    """Apply Config enable toggles; return None when the kind is suppressed."""
    if kind is None:
        return None
    if kind == HighValueKind.AMOUNT and not cfg.enable_amount_pattern:
        return None
    if kind == HighValueKind.DATE and not cfg.enable_date_pattern:
        return None
    if kind == HighValueKind.ID_LIKE and not cfg.enable_id_like_boost:
        return None
    return kind


def _collect_classes(object_diffs: Sequence[ObjectDiff]) -> tuple[ObjectChangeClass, ...]:
    """Sorted deduplicated tuple of every ObjectChangeClass seen."""
    seen: set[ObjectChangeClass] = set()
    for od in object_diffs:
        for ch in od.changes:
            seen.add(ch.change_class)
    return tuple(sorted(seen, key=lambda c: c.value))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score(
    text_changes: Sequence[TextChange],
    object_diffs: Sequence[ObjectDiff],
    recon_result: ReconstructionResult,
    config: Config | None = None,
) -> ScoringResult:
    """Apply the scoring rule tree and return a :class:`ScoringResult`.

    Parameters
    ----------
    text_changes:
        One :class:`TextChange` per consecutive revision pair (may be empty).
    object_diffs:
        One :class:`ObjectDiff` per consecutive revision pair (may be empty).
    recon_result:
        The full reconstruction outcome including any failures.
    config:
        Scoring / normalisation config.  ``None`` uses spec defaults.

    Returns
    -------
    ScoringResult
        Tier + score + evidence.  Never raises.
    """
    cfg = config or Config()

    n_revisions = recon_result.revision_count
    n_failures = len(recon_result.failures)
    total_detected = n_revisions + n_failures

    all_classes = _collect_classes(object_diffs)
    classes_set = set(all_classes)

    any_substantive = any(tc.is_substantive for tc in text_changes)
    any_hv_raw = any(tc.has_high_value_change for tc in text_changes)
    best_raw = _best_hv_kind(text_changes)
    effective_hv = _effective_hv_kind(best_raw, cfg)

    has_content = ObjectChangeClass.CONTENT in classes_set
    has_overlay = ObjectChangeClass.OVERLAY in classes_set
    has_field_edit = ObjectChangeClass.FIELD_EDIT in classes_set
    has_form_fill = ObjectChangeClass.FORM_FILL in classes_set

    common = dict(
        object_classes_seen=all_classes,
        has_substantive_text_change=any_substantive,
        has_high_value_change=any_hv_raw,
        high_value_kind=effective_hv,
        revision_count=n_revisions,
        has_reconstruction_failures=n_failures > 0,
    )

    # ------------------------------------------------------------------ #
    # INCONCLUSIVE — only one revision detected (single-revision PDF)     #
    # ------------------------------------------------------------------ #
    if total_detected <= 1:
        return ScoringResult(
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            reasons=("single revision found; route to later stages (font / OCR)",),
            notes=(),
            **common,
        )

    # ------------------------------------------------------------------ #
    # HIGH — substantive text diff that maps to a changed CONTENT object  #
    # ------------------------------------------------------------------ #
    if any_substantive and has_content:
        if effective_hv in (HighValueKind.AMOUNT, HighValueKind.DATE):
            score_val = cfg.score_high_amount_date
            tag = "high-value field altered"
            reasons: list[str] = [
                f"substantive text change in CONTENT object; "
                f"{tag} ({effective_hv.value} pattern matched)"
            ]
        elif effective_hv == HighValueKind.ID_LIKE:
            score_val = cfg.score_high_id_like
            tag = "possible policy/claim ID altered (ID-like match, weak)"
            reasons = [
                f"substantive text change in CONTENT object; {tag}"
            ]
        else:
            score_val = cfg.score_high_prose
            tag = "prose-only change"
            reasons = ["substantive text change in CONTENT object; prose-only tokens changed"]

        return ScoringResult(
            tier=ConfidenceTier.HIGH,
            score=score_val,
            reasons=tuple(reasons),
            notes=(),
            **common,
        )

    # ------------------------------------------------------------------ #
    # MEDIUM — any one of the listed conditions                           #
    # ------------------------------------------------------------------ #
    medium_score = 0
    medium_reasons: list[str] = []

    if has_content and not any_substantive:
        # CONTENT stream changed but text diff is empty — possible overlay /
        # inpainting that the text layer doesn't reflect; needs OCR cross-check.
        medium_score = max(medium_score, cfg.score_medium_content_no_text)
        medium_reasons.append(
            "CONTENT stream changed but normalized text diff is empty "
            "(possible overlay / inpainting; OCR cross-check recommended)"
        )

    if has_overlay:
        medium_score = max(medium_score, cfg.score_medium_overlay)
        medium_reasons.append("OVERLAY object changed (stamp, redaction, or covering annotation)")

    if has_field_edit:
        medium_score = max(medium_score, cfg.score_medium_field_edit)
        medium_reasons.append("form field value changed from a prior non-empty value (FIELD_EDIT)")

    if cfg.form_fill_triggers_medium and has_form_fill:
        medium_score = max(medium_score, cfg.score_medium_form_fill)
        medium_reasons.append(
            "form field filled for first time (FORM_FILL; form_fill_triggers_medium=True)"
        )

    if n_failures > 0:
        medium_score = max(medium_score, cfg.score_medium_recon_failure)
        noun = "revision" if n_failures == 1 else "revisions"
        medium_reasons.append(
            f"{n_failures} {noun} detected but could not be reconstructed "
            "(corruption or evasion)"
        )

    if medium_reasons:
        return ScoringResult(
            tier=ConfidenceTier.MEDIUM,
            score=medium_score,
            reasons=tuple(medium_reasons),
            notes=(),
            **common,
        )

    # ------------------------------------------------------------------ #
    # LOW — multiple revisions, changes confined to benign object types   #
    # ------------------------------------------------------------------ #
    if all_classes:
        class_names = ", ".join(c.value for c in all_classes)
        low_reason = f"multiple revisions; changes limited to benign object types ({class_names})"
    else:
        low_reason = "multiple revisions found; no object changes detected between pairs"

    return ScoringResult(
        tier=ConfidenceTier.LOW,
        score=cfg.score_low_default,
        reasons=(low_reason,),
        notes=(),
        **common,
    )
