"""Tests for scoring.py — tier/score boundaries from the rubric.

All tests use pure data-model construction (no real PDFs needed) so they are
fast and independent.  Fixtures build the minimal TextChange / ObjectDiff /
ReconstructionResult structures required to exercise each scoring rule.
"""

from __future__ import annotations

import pytest

from pdf_forgery.revision_recovery.config import Config
from pdf_forgery.revision_recovery.models import (
    CharSpan,
    ConfidenceTier,
    HighValueKind,
    ObjectChange,
    ObjectChangeClass,
    ObjectDiff,
    PageTextDiff,
    ReconstructionFailure,
    ReconstructionResult,
    Revision,
    ScoringResult,
    TextChange,
    TokenDiff,
)
from pdf_forgery.revision_recovery.scoring import score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tok(
    before: str = "old",
    after: str = "new",
    hv: HighValueKind | None = None,
) -> TokenDiff:
    return TokenDiff(before=before, after=after, char_diff=(), high_value=hv)


def _page_diff(
    is_substantive: bool,
    high_value_kind: HighValueKind | None = None,
    page_index: int = 0,
) -> PageTextDiff:
    toks = (_tok(hv=high_value_kind),) if is_substantive else ()
    return PageTextDiff(
        page_index=page_index,
        before_text="old text" if is_substantive else "same",
        after_text="new text" if is_substantive else "same",
        token_changes=toks,
        is_substantive=is_substantive,
        has_high_value_change=high_value_kind is not None,
    )


def _text_change(
    is_substantive: bool,
    high_value_kind: HighValueKind | None = None,
    from_rev: int = 0,
    to_rev: int = 1,
) -> TextChange:
    pd = _page_diff(is_substantive, high_value_kind)
    return TextChange(
        from_revision=from_rev,
        to_revision=to_rev,
        page_diffs=(pd,),
        is_substantive=is_substantive,
        has_high_value_change=high_value_kind is not None,
    )


def _obj_diff(
    *classes: ObjectChangeClass,
    from_rev: int = 0,
    to_rev: int = 1,
) -> ObjectDiff:
    changes = tuple(
        ObjectChange(
            obj_num=i + 1,
            gen_num=0,
            change_class=cls,
            page_index=0,
            is_new=False,
        )
        for i, cls in enumerate(classes)
    )
    return ObjectDiff(from_revision=from_rev, to_revision=to_rev, changes=changes)


def _recon(n_revisions: int, n_failures: int = 0) -> ReconstructionResult:
    revisions = tuple(
        Revision(
            index=i,
            source_boundary_index=i,
            truncate_len=100,
            page_count=1,
            is_encrypted=False,
            data=b"%PDF-1.4\n",
        )
        for i in range(n_revisions)
    )
    failures = tuple(
        ReconstructionFailure(
            source_boundary_index=n_revisions + i,
            truncate_len=50,
            reason="unloadable: test",
        )
        for i in range(n_failures)
    )
    return ReconstructionResult(revisions=revisions, failures=failures)


# ---------------------------------------------------------------------------
# INCONCLUSIVE
# ---------------------------------------------------------------------------


class TestInconclusive:
    def test_single_revision_no_failures(self):
        result = score([], [], _recon(1, 0))
        assert result.tier == ConfidenceTier.INCONCLUSIVE
        assert result.score is None

    def test_zero_revisions_no_failures(self):
        result = score([], [], _recon(0, 0))
        assert result.tier == ConfidenceTier.INCONCLUSIVE
        assert result.score is None

    def test_inconclusive_has_reason(self):
        result = score([], [], _recon(1))
        assert len(result.reasons) >= 1
        assert "single" in result.reasons[0].lower()

    def test_inconclusive_flags_state(self):
        result = score([], [], _recon(1))
        assert result.revision_count == 1
        assert result.has_reconstruction_failures is False
        assert result.has_substantive_text_change is False


# ---------------------------------------------------------------------------
# HIGH tier
# ---------------------------------------------------------------------------


class TestHigh:
    def test_amount_token_gives_high_score(self):
        tc = _text_change(True, HighValueKind.AMOUNT)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.HIGH
        assert result.score == Config().score_high_amount_date  # 95

    def test_date_token_gives_high_score(self):
        tc = _text_change(True, HighValueKind.DATE)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.HIGH
        assert result.score == Config().score_high_amount_date

    def test_id_like_with_boost_gives_id_like_score(self):
        tc = _text_change(True, HighValueKind.ID_LIKE)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.HIGH
        assert result.score == Config().score_high_id_like  # 85
        assert result.high_value_kind == HighValueKind.ID_LIKE

    def test_prose_only_gives_prose_score(self):
        tc = _text_change(True, None)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.HIGH
        assert result.score == Config().score_high_prose  # 75
        assert result.high_value_kind is None

    def test_id_like_with_boost_disabled_gives_prose_score(self):
        cfg = Config(enable_id_like_boost=False)
        tc = _text_change(True, HighValueKind.ID_LIKE)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2), cfg)
        assert result.tier == ConfidenceTier.HIGH
        assert result.score == cfg.score_high_prose
        assert result.high_value_kind is None

    def test_amount_pattern_disabled_falls_to_prose(self):
        cfg = Config(enable_amount_pattern=False)
        tc = _text_change(True, HighValueKind.AMOUNT)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2), cfg)
        assert result.tier == ConfidenceTier.HIGH
        assert result.score == cfg.score_high_prose
        assert result.high_value_kind is None

    def test_date_pattern_disabled_falls_to_prose(self):
        cfg = Config(enable_date_pattern=False)
        tc = _text_change(True, HighValueKind.DATE)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2), cfg)
        assert result.tier == ConfidenceTier.HIGH
        assert result.score == cfg.score_high_prose

    def test_high_wins_over_medium_when_both_present(self):
        # CONTENT changed + substantive text (→ HIGH) even when OVERLAY also present
        tc = _text_change(True, HighValueKind.AMOUNT)
        od = _obj_diff(ObjectChangeClass.CONTENT, ObjectChangeClass.OVERLAY)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.HIGH

    def test_high_wins_when_recon_failure_present(self):
        tc = _text_change(True, HighValueKind.AMOUNT)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2, 1))  # extra failure
        assert result.tier == ConfidenceTier.HIGH

    def test_custom_score_high_amount(self):
        cfg = Config(score_high_amount_date=98)
        tc = _text_change(True, HighValueKind.AMOUNT)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2), cfg)
        assert result.score == 98

    def test_custom_score_high_prose(self):
        cfg = Config(score_high_prose=72)
        tc = _text_change(True, None)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2), cfg)
        assert result.score == 72

    def test_high_requires_content_object_change(self):
        # Substantive text but only SIGNATURE changed → not HIGH
        tc = _text_change(True, HighValueKind.AMOUNT)
        od = _obj_diff(ObjectChangeClass.SIGNATURE)
        result = score([tc], [od], _recon(2))
        assert result.tier != ConfidenceTier.HIGH

    def test_high_requires_substantive_text(self):
        # CONTENT changed but no text diff → MEDIUM, not HIGH
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.MEDIUM

    def test_high_result_fields(self):
        tc = _text_change(True, HighValueKind.DATE)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2))
        assert result.has_substantive_text_change is True
        assert result.has_high_value_change is True
        assert result.high_value_kind == HighValueKind.DATE
        assert ObjectChangeClass.CONTENT in result.object_classes_seen
        assert result.revision_count == 2
        assert result.has_reconstruction_failures is False

    def test_amount_is_higher_priority_than_id_like(self):
        # Page 0: ID_LIKE; page 1: AMOUNT — best should be AMOUNT
        pd0 = _page_diff(True, HighValueKind.ID_LIKE, page_index=0)
        pd1 = _page_diff(True, HighValueKind.AMOUNT, page_index=1)
        tc = TextChange(
            from_revision=0, to_revision=1,
            page_diffs=(pd0, pd1),
            is_substantive=True, has_high_value_change=True,
        )
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2))
        assert result.high_value_kind == HighValueKind.AMOUNT
        assert result.score == Config().score_high_amount_date


# ---------------------------------------------------------------------------
# MEDIUM tier
# ---------------------------------------------------------------------------


class TestMedium:
    def test_content_changed_but_no_text_diff(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.MEDIUM
        assert result.score == Config().score_medium_content_no_text  # 60

    def test_overlay_change(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.OVERLAY)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.MEDIUM
        assert result.score == Config().score_medium_overlay  # 55

    def test_field_edit(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.FIELD_EDIT)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.MEDIUM
        assert result.score == Config().score_medium_field_edit  # 50

    def test_recon_failure_alone_triggers_medium(self):
        # 1 success + 1 failure → 2 detected → multi-revision → MEDIUM
        result = score([], [], _recon(1, 1))
        assert result.tier == ConfidenceTier.MEDIUM
        assert result.score == Config().score_medium_recon_failure  # 40

    def test_two_failures_no_revisions(self):
        result = score([], [], _recon(0, 2))
        assert result.tier == ConfidenceTier.MEDIUM

    def test_form_fill_default_is_low(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.FORM_FILL)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.LOW  # default: benign

    def test_form_fill_triggers_medium_when_enabled(self):
        cfg = Config(form_fill_triggers_medium=True)
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.FORM_FILL)
        result = score([tc], [od], _recon(2), cfg)
        assert result.tier == ConfidenceTier.MEDIUM
        assert result.score == cfg.score_medium_form_fill  # 45

    def test_multiple_medium_conditions_takes_max(self):
        # CONTENT no-text (60) + OVERLAY (55): max = 60
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.CONTENT, ObjectChangeClass.OVERLAY)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.MEDIUM
        assert result.score == Config().score_medium_content_no_text

    def test_overlay_and_field_edit_takes_overlay_score(self):
        # OVERLAY (55) > FIELD_EDIT (50) → 55
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.OVERLAY, ObjectChangeClass.FIELD_EDIT)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.MEDIUM
        assert result.score == Config().score_medium_overlay

    def test_custom_score_medium_overlay(self):
        cfg = Config(score_medium_overlay=65)
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.OVERLAY)
        result = score([tc], [od], _recon(2), cfg)
        assert result.score == 65

    def test_custom_score_medium_recon_failure(self):
        cfg = Config(score_medium_recon_failure=35)
        result = score([], [], _recon(1, 1), cfg)
        assert result.score == 35

    def test_medium_result_has_reasons(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2))
        assert len(result.reasons) >= 1
        reason_text = " ".join(result.reasons).lower()
        assert "content" in reason_text

    def test_medium_flags_reconstruction_failure(self):
        result = score([], [], _recon(1, 2))
        assert result.has_reconstruction_failures is True
        assert result.revision_count == 1


# ---------------------------------------------------------------------------
# LOW tier
# ---------------------------------------------------------------------------


class TestLow:
    def test_signature_only_is_low(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.SIGNATURE)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.LOW
        assert result.score == Config().score_low_default  # 15

    def test_meta_only_is_low(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.META)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.LOW

    def test_markup_only_is_low(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.MARKUP)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.LOW

    def test_form_fill_default_is_low(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.FORM_FILL)
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.LOW

    def test_mixed_benign_classes_is_low(self):
        tc = _text_change(False)
        od = _obj_diff(
            ObjectChangeClass.SIGNATURE,
            ObjectChangeClass.META,
            ObjectChangeClass.MARKUP,
            ObjectChangeClass.FORM_FILL,
        )
        result = score([tc], [od], _recon(2))
        assert result.tier == ConfidenceTier.LOW

    def test_no_changes_multi_revision_is_low(self):
        # Empty object diffs: two revisions but nothing changed
        od = _obj_diff()  # zero changes
        result = score([], [od], _recon(2))
        assert result.tier == ConfidenceTier.LOW

    def test_custom_score_low(self):
        cfg = Config(score_low_default=10)
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.SIGNATURE)
        result = score([tc], [od], _recon(2), cfg)
        assert result.score == 10

    def test_low_has_reason(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.SIGNATURE)
        result = score([tc], [od], _recon(2))
        assert len(result.reasons) >= 1

    def test_low_object_classes_populated(self):
        tc = _text_change(False)
        od = _obj_diff(ObjectChangeClass.SIGNATURE, ObjectChangeClass.META)
        result = score([tc], [od], _recon(2))
        assert ObjectChangeClass.SIGNATURE in result.object_classes_seen
        assert ObjectChangeClass.META in result.object_classes_seen


# ---------------------------------------------------------------------------
# ScoringResult field correctness
# ---------------------------------------------------------------------------


class TestResultFields:
    def test_inconclusive_score_is_none(self):
        result = score([], [], _recon(1))
        assert result.score is None

    def test_all_tiers_return_scoring_result(self):
        configs = [
            ([], [], _recon(1)),                              # INCONCLUSIVE
            ([_text_change(False)], [_obj_diff(ObjectChangeClass.SIGNATURE)], _recon(2)),  # LOW
            ([_text_change(False)], [_obj_diff(ObjectChangeClass.OVERLAY)], _recon(2)),    # MEDIUM
            ([_text_change(True, HighValueKind.AMOUNT)], [_obj_diff(ObjectChangeClass.CONTENT)], _recon(2)),  # HIGH
        ]
        for args in configs:
            result = score(*args)
            assert isinstance(result, ScoringResult)

    def test_object_classes_seen_deduplicated(self):
        # Two pairs both reporting CONTENT — should appear once
        od1 = _obj_diff(ObjectChangeClass.CONTENT, from_rev=0, to_rev=1)
        od2 = _obj_diff(ObjectChangeClass.CONTENT, from_rev=1, to_rev=2)
        tc1 = _text_change(True, None, from_rev=0, to_rev=1)
        tc2 = _text_change(True, None, from_rev=1, to_rev=2)
        result = score([tc1, tc2], [od1, od2], _recon(3))
        assert result.object_classes_seen.count(ObjectChangeClass.CONTENT) == 1

    def test_has_substantive_text_change_across_pairs(self):
        tc1 = _text_change(False, from_rev=0, to_rev=1)  # non-substantive
        tc2 = _text_change(True, None, from_rev=1, to_rev=2)  # substantive
        od1 = _obj_diff(ObjectChangeClass.SIGNATURE, from_rev=0, to_rev=1)
        od2 = _obj_diff(ObjectChangeClass.CONTENT, from_rev=1, to_rev=2)
        result = score([tc1, tc2], [od1, od2], _recon(3))
        assert result.has_substantive_text_change is True

    def test_high_value_kind_none_when_all_disabled(self):
        cfg = Config(
            enable_amount_pattern=False,
            enable_date_pattern=False,
            enable_id_like_boost=False,
        )
        tc = _text_change(True, HighValueKind.AMOUNT)
        od = _obj_diff(ObjectChangeClass.CONTENT)
        result = score([tc], [od], _recon(2), cfg)
        assert result.high_value_kind is None
        # Still HIGH, just prose score
        assert result.tier == ConfidenceTier.HIGH
        assert result.score == cfg.score_high_prose

    def test_notes_tuple(self):
        result = score([], [], _recon(1))
        assert isinstance(result.notes, tuple)

    def test_reasons_tuple(self):
        result = score([], [], _recon(1))
        assert isinstance(result.reasons, tuple)
        assert all(isinstance(r, str) for r in result.reasons)
