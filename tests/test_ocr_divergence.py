"""Tests for Stage 3 divergence classification (§2, §6a).

Covers weight_for(), classify_group(), classify_unmatched(), classify_page().
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from pdf_forgery.ocr_crosscheck.config import OCRCrossCheckConfig
from pdf_forgery.ocr_crosscheck.divergence import (
    classify_group,
    classify_page,
    classify_unmatched,
    weight_for,
)
from pdf_forgery.ocr_crosscheck.models import (
    DivergenceType,
    TokenClass,
    WordBox,
    WordSource,
)


def _emb(text, x0=0, y0=0, x1=100, y1=20, page=0):
    return WordBox(
        text=text, bbox=(x0, y0, x1, y1),
        source=WordSource.EMBEDDED, conf=None, page_index=page,
    )


def _ocr(text, x0=0, y0=0, x1=100, y1=20, conf=0.9, page=0):
    return WordBox(
        text=text, bbox=(x0, y0, x1, y1),
        source=WordSource.OCR, conf=conf, page_index=page,
    )


# ---------------------------------------------------------------------------
# weight_for()
# ---------------------------------------------------------------------------

class TestWeightFor:
    def test_agree_is_zero(self):
        assert weight_for(DivergenceType.AGREE, TokenClass.PROSE) == 0.0
        assert weight_for(DivergenceType.AGREE, TokenClass.AMOUNT) == 0.0

    def test_mismatch_prose(self):
        cfg = OCRCrossCheckConfig(weight_mismatch=1.0, mult_prose=1.0)
        assert weight_for(DivergenceType.MISMATCH, TokenClass.PROSE, cfg) == 1.0

    def test_mismatch_amount(self):
        cfg = OCRCrossCheckConfig(weight_mismatch=1.0, mult_amount=3.0)
        assert weight_for(DivergenceType.MISMATCH, TokenClass.AMOUNT, cfg) == 3.0

    def test_mismatch_date(self):
        cfg = OCRCrossCheckConfig(weight_mismatch=1.0, mult_date=3.0)
        assert weight_for(DivergenceType.MISMATCH, TokenClass.DATE, cfg) == 3.0

    def test_mismatch_id(self):
        cfg = OCRCrossCheckConfig(weight_mismatch=1.0, mult_id=1.5)
        assert weight_for(DivergenceType.MISMATCH, TokenClass.ID, cfg) == 1.5

    def test_embedded_only_amount(self):
        cfg = OCRCrossCheckConfig(weight_embedded_only=0.6, mult_amount=3.0)
        assert weight_for(DivergenceType.EMBEDDED_ONLY, TokenClass.AMOUNT, cfg) == pytest.approx(1.8)

    def test_ocr_only_prose(self):
        cfg = OCRCrossCheckConfig(weight_ocr_only=0.7, mult_prose=1.0)
        assert weight_for(DivergenceType.OCR_ONLY, TokenClass.PROSE, cfg) == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# classify_group()
# ---------------------------------------------------------------------------

class TestClassifyGroup:
    def test_identical_prose_agree(self):
        emb = (_emb("hello"),)
        ocr = _ocr("hello")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.AGREE
        assert d.token_class is TokenClass.PROSE
        assert d.weight == 0.0

    def test_matching_amount_agree(self):
        emb = (_emb("5,000"),)
        ocr = _ocr("5,000")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.AGREE
        assert d.token_class is TokenClass.AMOUNT

    def test_mismatched_amount_mismatch(self):
        emb = (_emb("5,000"),)
        ocr = _ocr("50,000")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.MISMATCH
        assert d.token_class is TokenClass.AMOUNT

    def test_mismatch_amount_high_weight(self):
        emb = (_emb("5,000"),)
        ocr = _ocr("50,000")
        d = classify_group(emb, ocr)
        # weight = weight_mismatch * mult_amount = 1.0 * 3.0
        assert d.weight == pytest.approx(3.0)

    def test_one_to_many_agree(self):
        # "Rs" and "5,000" as separate embedded words, OCR groups as "Rs 5,000"
        emb = (_emb("Rs"), _emb("5,000"))
        ocr = _ocr("Rs 5,000")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.AGREE
        assert d.token_class is TokenClass.AMOUNT  # '5,000' is AMOUNT

    def test_one_to_many_mismatch(self):
        # "Rs" + "5,000" embedded, OCR sees "Rs 50,000"
        emb = (_emb("Rs"), _emb("5,000"))
        ocr = _ocr("Rs 50,000")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.MISMATCH
        assert d.token_class is TokenClass.AMOUNT

    def test_prose_within_tolerance(self):
        # Minor OCR noise in prose: "helo" vs "hello" — 1 edit, prose allows 1
        emb = (_emb("hello"),)
        ocr = _ocr("helo")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.AGREE

    def test_prose_beyond_tolerance(self):
        emb = (_emb("hello"),)
        ocr = _ocr("world")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.MISMATCH

    def test_high_value_token_diluted_in_group(self):
        # Group contains PROSE + AMOUNT — token class is AMOUNT (most sensitive)
        # so zero tolerance applies to the whole group
        emb = (_emb("Approved"), _emb("5,000"))
        ocr = _ocr("Approved 5,001")  # 1 char difference in amount
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.MISMATCH
        assert d.token_class is TokenClass.AMOUNT

    def test_confusion_fold_agree(self):
        # 'O' and '0' confusion — should agree after fold
        emb = (_emb("1OO"),)
        ocr = _ocr("100")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.AGREE

    def test_page_index_propagated(self):
        emb = (_emb("word", page=2),)
        ocr = _ocr("word", page=2)
        d = classify_group(emb, ocr)
        assert d.page_index == 2

    def test_embedded_tuple_preserved(self):
        e1, e2 = _emb("Rs"), _emb("5,000")
        emb = (e1, e2)
        d = classify_group(emb, _ocr("Rs 5,000"))
        assert d.embedded == emb


# ---------------------------------------------------------------------------
# RC#1 (docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md) — bare-digit AMOUNT elevation
# + localized high-value tolerance for multi-token groups
# ---------------------------------------------------------------------------

class TestRC1BareDigitAndLocalizedTolerance:
    def test_bare_digit_term_line_agrees(self):
        # Real Azure-invoice case: a bare "3" (reservation term, years) used to
        # elevate the whole line to AMOUNT (zero tolerance); an unrelated
        # underscore-vs-space OCR glyph drop then tripped a false MISMATCH.
        emb = (_emb("Standard_M64s,"), _emb("AU"), _emb("East,"), _emb("3"))
        ocr = _ocr("Standard M64s, AU East, 3")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.AGREE

    def test_bare_digit_alone_classifies_prose_not_amount(self):
        emb = (_emb("3"),)
        ocr = _ocr("3")
        d = classify_group(emb, ocr)
        assert d.token_class is not TokenClass.AMOUNT

    def test_genuine_amount_edit_in_multi_token_line_still_mismatches(self):
        # Regression guard: a REAL altered amount inside a multi-token line
        # must still be caught — the localized check must not blind the signal.
        emb = (_emb("Total:"), _emb("5,000"))
        ocr = _ocr("Total: 6,000")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.MISMATCH
        assert d.token_class is TokenClass.AMOUNT

    def test_genuine_date_edit_in_multi_token_line_still_mismatches(self):
        emb = (_emb("Due:"), _emb("01/06/2024"))
        ocr = _ocr("Due: 01/07/2024")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.MISMATCH
        assert d.token_class is TokenClass.DATE

    def test_single_token_amount_edit_still_strict(self):
        # Single-token high-value groups keep the original strict whole-string
        # check (no behaviour change from RC#1's localized-tolerance fix).
        emb = (_emb("5,000"),)
        ocr = _ocr("5,001")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.MISMATCH
        assert d.token_class is TokenClass.AMOUNT

    def test_prose_noise_elsewhere_in_amount_line_agrees(self):
        # Amount intact; unrelated prose-side OCR noise within prose tolerance.
        emb = (_emb("Helo"), _emb("5,000"))
        ocr = _ocr("Hello 5,000")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.AGREE

    def test_prose_noise_beyond_tolerance_mismatches_even_if_amount_intact(self):
        emb = (_emb("Completely"), _emb("Different"), _emb("Words"), _emb("5,000"))
        ocr = _ocr("Xyzzqq Plonk Whoa 5,000")
        d = classify_group(emb, ocr)
        assert d.type is DivergenceType.MISMATCH
        assert d.token_class is TokenClass.AMOUNT


# ---------------------------------------------------------------------------
# classify_unmatched()
# ---------------------------------------------------------------------------

class TestClassifyUnmatched:
    def test_embedded_only_prose(self):
        w = _emb("hello")
        d = classify_unmatched(w)
        assert d.type is DivergenceType.EMBEDDED_ONLY
        assert d.embedded == (w,)
        assert d.ocr is None

    def test_ocr_only_prose(self):
        w = _ocr("world")
        d = classify_unmatched(w)
        assert d.type is DivergenceType.OCR_ONLY
        assert d.embedded == ()
        assert d.ocr is w

    def test_embedded_only_amount_class(self):
        w = _emb("5,000")
        d = classify_unmatched(w)
        assert d.token_class is TokenClass.AMOUNT

    def test_ocr_only_amount_class(self):
        w = _ocr("₹1,20,000")
        d = classify_unmatched(w)
        assert d.token_class is TokenClass.AMOUNT

    def test_embedded_only_id_class(self):
        w = _emb("CHI1234567")
        d = classify_unmatched(w)
        assert d.token_class is TokenClass.ID

    def test_embedded_only_date_class(self):
        w = _emb("01/06/2024")
        d = classify_unmatched(w)
        assert d.token_class is TokenClass.DATE

    def test_embedded_only_weight(self):
        # EMBEDDED_ONLY prose: 0.6 * 1.0 = 0.6
        w = _emb("hello")
        d = classify_unmatched(w)
        assert d.weight == pytest.approx(0.6)

    def test_ocr_only_amount_weight(self):
        # OCR_ONLY amount: 0.7 * 3.0 = 2.1
        w = _ocr("5,000")
        d = classify_unmatched(w)
        assert d.weight == pytest.approx(2.1)

    def test_embedded_only_amount_weight(self):
        # EMBEDDED_ONLY amount: 0.6 * 3.0 = 1.8
        w = _emb("5,000")
        d = classify_unmatched(w)
        assert d.weight == pytest.approx(1.8)

    def test_page_index_preserved(self):
        w = _emb("word", page=3)
        d = classify_unmatched(w)
        assert d.page_index == 3


# ---------------------------------------------------------------------------
# classify_page()
# ---------------------------------------------------------------------------

class TestClassifyPage:
    def test_empty_inputs(self):
        result = classify_page([], [], [])
        assert result == []

    def test_all_agree(self):
        groups = [
            ((_emb("hello"),), _ocr("hello")),
            ((_emb("world"),), _ocr("world")),
        ]
        result = classify_page(groups, [], [])
        assert len(result) == 2
        assert all(d.type is DivergenceType.AGREE for d in result)

    def test_mismatch_group(self):
        groups = [((_emb("5,000"),), _ocr("50,000"))]
        result = classify_page(groups, [], [])
        assert len(result) == 1
        assert result[0].type is DivergenceType.MISMATCH

    def test_unmatched_embedded(self):
        result = classify_page([], [_emb("hidden")], [])
        assert len(result) == 1
        assert result[0].type is DivergenceType.EMBEDDED_ONLY

    def test_unmatched_ocr(self):
        result = classify_page([], [], [_ocr("overlay")])
        assert len(result) == 1
        assert result[0].type is DivergenceType.OCR_ONLY

    def test_mixed_results(self):
        groups = [
            ((_emb("hello"),), _ocr("hello")),
            ((_emb("5,000"),), _ocr("50,000")),
        ]
        unm_emb = [_emb("hidden")]
        unm_ocr = [_ocr("overlay")]
        result = classify_page(groups, unm_emb, unm_ocr)
        assert len(result) == 4
        types = [d.type for d in result]
        assert DivergenceType.AGREE in types
        assert DivergenceType.MISMATCH in types
        assert DivergenceType.EMBEDDED_ONLY in types
        assert DivergenceType.OCR_ONLY in types

    def test_order_groups_then_unmatched_emb_then_unmatched_ocr(self):
        groups = [((_emb("a"),), _ocr("a"))]
        unm_emb = [_emb("b")]
        unm_ocr = [_ocr("c")]
        result = classify_page(groups, unm_emb, unm_ocr)
        assert result[0].type is DivergenceType.AGREE
        assert result[1].type is DivergenceType.EMBEDDED_ONLY
        assert result[2].type is DivergenceType.OCR_ONLY
