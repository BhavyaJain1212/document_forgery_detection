"""Tests for Stage 3 scoring rule tree (§6).

Verifies the INCONCLUSIVE/HIGH/MEDIUM/LOW tiers and the correct score values
per the locked rubric in docs/STAGE3_DESIGN.md.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from pdf_forgery.core.types import ConfidenceTier
from pdf_forgery.ocr_crosscheck.config import OCRCrossCheckConfig
from pdf_forgery.ocr_crosscheck.models import (
    Divergence,
    DivergenceType,
    TokenClass,
    WordBox,
    WordSource,
)
from pdf_forgery.ocr_crosscheck.scoring import score


def _emb_word(text="word"):
    return WordBox(text=text, bbox=(0, 0, 50, 10),
                   source=WordSource.EMBEDDED, conf=None, page_index=0)


def _ocr_word(text="word"):
    return WordBox(text=text, bbox=(0, 0, 50, 10),
                   source=WordSource.OCR, conf=0.9, page_index=0)


def _div(div_type, token_class, weight=1.0, text="word"):
    emb = (_emb_word(text),) if div_type in (DivergenceType.AGREE, DivergenceType.MISMATCH,
                                              DivergenceType.EMBEDDED_ONLY) else ()
    ocr = _ocr_word(text) if div_type in (DivergenceType.AGREE, DivergenceType.MISMATCH,
                                           DivergenceType.OCR_ONLY) else None
    return Divergence(
        type=div_type, embedded=emb, ocr=ocr,
        token_class=token_class, weight=weight, page_index=0,
    )


CFG = OCRCrossCheckConfig(
    medium_divergence_mass=2.0,
    score_high_amount_date_mismatch=95,
    score_high_value_orphan=75,
    min_high_value_orphans=2,
    score_medium_default=50,
    score_low_default=15,
)


# ---------------------------------------------------------------------------
# INCONCLUSIVE paths
# ---------------------------------------------------------------------------

class TestScoringInconclusive:
    def test_routed_to_set(self):
        result = score([], routed_to="image_forensics", config=CFG)
        assert result.tier is ConfidenceTier.INCONCLUSIVE
        assert result.score is None
        assert result.routed_to == "image_forensics"

    def test_empty_divergences(self):
        result = score([], config=CFG)
        assert result.tier is ConfidenceTier.INCONCLUSIVE
        assert result.score is None
        assert result.routed_to is None

    def test_routed_with_divergences_still_inconclusive(self):
        divs = [_div(DivergenceType.MISMATCH, TokenClass.AMOUNT, 3.0)]
        result = score(divs, routed_to="image_forensics", config=CFG)
        assert result.tier is ConfidenceTier.INCONCLUSIVE  # routed takes priority


# ---------------------------------------------------------------------------
# HIGH paths
# ---------------------------------------------------------------------------

class TestScoringHigh:
    def test_amount_mismatch_high(self):
        divs = [_div(DivergenceType.MISMATCH, TokenClass.AMOUNT, 3.0)]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.HIGH
        assert result.score == 95

    def test_date_mismatch_high(self):
        divs = [_div(DivergenceType.MISMATCH, TokenClass.DATE, 3.0)]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.HIGH
        assert result.score == 95

    def test_id_mismatch_not_high(self):
        # Fix #2: ID never originates HIGH (Stage 1's own "ID = weak" rule).
        # A lone ID MISMATCH contributes its weight to mass instead (1.5 < 2.0 → LOW).
        divs = [_div(DivergenceType.MISMATCH, TokenClass.ID, 1.5)]
        result = score(divs, config=CFG)
        assert result.tier is not ConfidenceTier.HIGH
        assert result.tier is ConfidenceTier.LOW
        assert result.score == 15

    def test_id_mismatches_reach_medium_via_mass(self):
        # ID MISMATCHes still accumulate mass toward MEDIUM, just never HIGH.
        divs = [
            _div(DivergenceType.MISMATCH, TokenClass.ID, 1.5),
            _div(DivergenceType.MISMATCH, TokenClass.ID, 1.5),
        ]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.MEDIUM
        assert result.score == 50

    def test_amount_embedded_only_lone_orphan_medium(self):
        # Fix #3: an UNCORROBORATED lone high-value orphan is MEDIUM, not HIGH —
        # it's the expected steady-state OCR noise on a clean page.
        divs = [
            Divergence(
                type=DivergenceType.EMBEDDED_ONLY,
                embedded=(_emb_word("5,000"),),
                ocr=None,
                token_class=TokenClass.AMOUNT,
                weight=1.8,
                page_index=0,
            )
        ]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.MEDIUM
        assert result.score == 50

    def test_amount_ocr_only_lone_orphan_medium(self):
        divs = [
            Divergence(
                type=DivergenceType.OCR_ONLY,
                embedded=(),
                ocr=_ocr_word("5,000"),
                token_class=TokenClass.AMOUNT,
                weight=2.1,
                page_index=0,
            )
        ]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.MEDIUM
        assert result.score == 50

    def test_date_embedded_only_lone_orphan_medium(self):
        divs = [
            Divergence(
                type=DivergenceType.EMBEDDED_ONLY,
                embedded=(_emb_word("01/06/2024"),),
                ocr=None,
                token_class=TokenClass.DATE,
                weight=1.8,
                page_index=0,
            )
        ]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.MEDIUM
        assert result.score == 50

    def test_two_amount_orphans_corroborate_high(self):
        # Fix #3: >= min_high_value_orphans (2) high-value orphans DO reach HIGH.
        divs = [
            Divergence(
                type=DivergenceType.EMBEDDED_ONLY,
                embedded=(_emb_word("5,000"),),
                ocr=None,
                token_class=TokenClass.AMOUNT,
                weight=1.8,
                page_index=0,
            ),
            Divergence(
                type=DivergenceType.OCR_ONLY,
                embedded=(),
                ocr=_ocr_word("01/06/2024"),
                token_class=TokenClass.DATE,
                weight=2.1,
                page_index=0,
            ),
        ]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.HIGH
        assert result.score == 75

    def test_orphan_corroborated_by_mismatch_high(self):
        # A high-value MISMATCH elsewhere also corroborates a lone orphan.
        divs = [
            _div(DivergenceType.MISMATCH, TokenClass.AMOUNT, 3.0),
            Divergence(
                type=DivergenceType.EMBEDDED_ONLY,
                embedded=(_emb_word("01/06/2024"),),
                ocr=None,
                token_class=TokenClass.DATE,
                weight=1.8,
                page_index=0,
            ),
        ]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.HIGH
        assert result.score == 95  # MISMATCH score still wins (max)

    def test_amount_mismatch_beats_id_mismatch(self):
        # When both present, highest score wins; ID never contends for HIGH.
        divs = [
            _div(DivergenceType.MISMATCH, TokenClass.AMOUNT, 3.0),
            _div(DivergenceType.MISMATCH, TokenClass.ID, 1.5),
        ]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.HIGH
        assert result.score == 95  # AMOUNT score wins

    def test_high_beats_medium_mass(self):
        # Even with large prose mass, a high-value MISMATCH → HIGH
        divs = [
            _div(DivergenceType.MISMATCH, TokenClass.AMOUNT, 3.0),
        ] + [_div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0) for _ in range(10)]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.HIGH

    def test_prose_mismatch_not_high(self):
        # PROSE MISMATCH alone is not HIGH
        divs = [_div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0)]
        result = score(divs, config=CFG)
        assert result.tier is not ConfidenceTier.HIGH


# ---------------------------------------------------------------------------
# MEDIUM paths
# ---------------------------------------------------------------------------

class TestScoringMedium:
    def test_prose_mismatch_high_mass(self):
        # mass = 2 * 1.0 = 2.0 >= medium_divergence_mass → MEDIUM
        divs = [
            _div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0),
            _div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0),
        ]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.MEDIUM
        assert result.score == 50

    def test_ocr_only_prose_medium(self):
        # 3 DISTINCT OCR-only prose words: mass = 3 * 0.7 = 2.1 >= 2.0
        # (distinct text so the repeated-orphan cap, RC#2 fix F, doesn't collapse them)
        divs = [
            _div(DivergenceType.OCR_ONLY, TokenClass.PROSE, 0.7, text=f"word{i}")
            for i in range(3)
        ]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.MEDIUM

    def test_mass_just_at_threshold(self):
        # mass exactly equals medium_divergence_mass → MEDIUM
        divs = [_div(DivergenceType.MISMATCH, TokenClass.PROSE, 2.0)]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.MEDIUM

    def test_agree_not_counted_in_mass(self):
        # Mass = 0 if only AGREE — not MEDIUM
        divs = [
            _div(DivergenceType.AGREE, TokenClass.PROSE, 0.0),
            _div(DivergenceType.AGREE, TokenClass.AMOUNT, 0.0),
        ]
        result = score(divs, config=CFG)
        assert result.tier is not ConfidenceTier.MEDIUM


# ---------------------------------------------------------------------------
# LOW paths
# ---------------------------------------------------------------------------

class TestScoringLow:
    def test_prose_mismatch_low_mass(self):
        # mass < medium_divergence_mass → LOW
        divs = [_div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0)]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.LOW
        assert result.score == 15

    def test_single_ocr_only_prose_low(self):
        divs = [_div(DivergenceType.OCR_ONLY, TokenClass.PROSE, 0.7)]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.LOW

    def test_all_agree_low_score_zero(self):
        # All divergences are AGREE → mass=0, not INCONCLUSIVE (we DID compare something)
        divs = [
            _div(DivergenceType.AGREE, TokenClass.PROSE, 0.0),
            _div(DivergenceType.AGREE, TokenClass.AMOUNT, 0.0),
        ]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.LOW
        assert result.score == 0

    def test_divergences_preserved_in_result(self):
        divs = [_div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0)]
        result = score(divs, config=CFG)
        assert len(result.divergences) == 1

    def test_custom_medium_threshold(self):
        cfg = OCRCrossCheckConfig(medium_divergence_mass=5.0)
        # mass = 4 * 1.0 = 4.0 < 5.0 → LOW
        divs = [_div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0) for _ in range(4)]
        result = score(divs, config=cfg)
        assert result.tier is ConfidenceTier.LOW


# ---------------------------------------------------------------------------
# RC#2 fix E — relative MEDIUM mass threshold (docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md)
# ---------------------------------------------------------------------------

class TestRelativeMassThreshold:
    def test_compared_words_default_zero_keeps_absolute_floor(self):
        # No compared_words passed → absolute-floor-only behaviour (back-compat).
        divs = [_div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0, text=f"w{i}")
                for i in range(3)]  # mass = 3.0 >= 2.0
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.MEDIUM

    def test_long_document_steady_state_noise_stays_low(self):
        # Same mass (3.0) as above, but compared_words is large (a long
        # document): relative floor = 0.02 * 200 = 4.0 > mass → LOW.
        divs = [_div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0, text=f"w{i}")
                for i in range(3)]
        result = score(divs, config=CFG, compared_words=200)
        assert result.tier is ConfidenceTier.LOW

    def test_short_document_real_cluster_still_medium(self):
        # Same mass (3.0), but compared_words is small: relative floor =
        # 0.02 * 50 = 1.0 < absolute floor 2.0 → absolute floor still governs,
        # a real dense cluster on a short doc reaches MEDIUM.
        divs = [_div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0, text=f"w{i}")
                for i in range(3)]
        result = score(divs, config=CFG, compared_words=50)
        assert result.tier is ConfidenceTier.MEDIUM

    def test_relative_floor_configurable(self):
        cfg = OCRCrossCheckConfig(
            medium_divergence_mass=2.0, divergence_mass_ratio=0.1,
        )
        divs = [_div(DivergenceType.MISMATCH, TokenClass.PROSE, 1.0, text=f"w{i}")
                for i in range(3)]  # mass = 3.0
        # relative floor = 0.1 * 40 = 4.0 > mass → LOW
        result = score(divs, config=cfg, compared_words=40)
        assert result.tier is ConfidenceTier.LOW


# ---------------------------------------------------------------------------
# RC#2 fix F — collapse repeated identical orphans (docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md)
# ---------------------------------------------------------------------------

class TestRepeatedOrphanCollapse:
    def test_repeated_identical_orphan_collapses_below_medium(self):
        # 5 identical-text OCR_ONLY PROSE orphans (e.g. a header repeated once
        # per page): uncapped mass = 5 * 0.7 = 3.5 (would be MEDIUM); capped at
        # repeated_orphan_cap=2 → mass = 1.4 < 2.0 → LOW.
        divs = [_div(DivergenceType.OCR_ONLY, TokenClass.PROSE, 0.7, text="Microsoft Azure")
                for _ in range(5)]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.LOW

    def test_distinct_orphans_not_collapsed(self):
        # Same count and weight, but DISTINCT text → no collapsing → MEDIUM.
        divs = [_div(DivergenceType.OCR_ONLY, TokenClass.PROSE, 0.7, text=f"word{i}")
                for i in range(5)]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.MEDIUM

    def test_repeated_orphan_cap_configurable(self):
        # Raising the cap to 5 means all 5 instances count → mass = 3.5 → MEDIUM.
        cfg = OCRCrossCheckConfig(
            medium_divergence_mass=2.0, repeated_orphan_cap=5,
        )
        divs = [_div(DivergenceType.OCR_ONLY, TokenClass.PROSE, 0.7, text="Microsoft Azure")
                for _ in range(5)]
        result = score(divs, config=cfg)
        assert result.tier is ConfidenceTier.MEDIUM

    def test_repeated_orphan_collapse_folds_case_and_space(self):
        # Folding (casefold + space removal) means "Microsoft Azure" and
        # "microsoft azure" are the SAME orphan key, not 2 distinct ones.
        texts = ["Microsoft Azure", "microsoft azure", "Microsoft Azure",
                 "MICROSOFT AZURE", "Microsoft  Azure"]
        divs = [_div(DivergenceType.OCR_ONLY, TokenClass.PROSE, 0.7, text=t)
                for t in texts]
        result = score(divs, config=CFG)
        assert result.tier is ConfidenceTier.LOW

    def test_real_invoice_mass_after_cap_matches_expectation(self):
        # The actual residual mass from the Microsoft-Sample-Invoice_clear.pdf
        # false positive (RC#2): 14 OCR_ONLY/ID @1.05 (repeated header) + 2
        # EMBEDDED_ONLY/ID @0.90 (distinct) — capped, this must land LOW.
        divs = (
            [_div(DivergenceType.OCR_ONLY, TokenClass.ID, 1.05, text="Microsoft Azure")
             for _ in range(14)]
            + [_div(DivergenceType.EMBEDDED_ONLY, TokenClass.ID, 0.90, text="Azure Logo")]
            + [_div(DivergenceType.EMBEDDED_ONLY, TokenClass.ID, 0.90, text="Azure Mark")]
        )
        result = score(divs, config=CFG, compared_words=2204)
        assert result.tier is ConfidenceTier.LOW
