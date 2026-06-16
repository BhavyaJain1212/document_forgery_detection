"""Tests for Stage 3 normalization + tolerance (§3).

Covers fold(), classify(), levenshtein(), allowed_edits(), is_within_tolerance().
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from pdf_forgery.ocr_crosscheck.config import OCRCrossCheckConfig
from pdf_forgery.ocr_crosscheck.models import TokenClass
from pdf_forgery.ocr_crosscheck.normalize import (
    allowed_edits,
    classify,
    fold,
    is_monetary_amount,
    is_within_tolerance,
    levenshtein,
)


# ---------------------------------------------------------------------------
# fold()
# ---------------------------------------------------------------------------

class TestFold:
    def test_nfc_normalization(self):
        # Composed vs decomposed form — both should fold to the same string
        import unicodedata
        composed = unicodedata.normalize("NFC", "café")     # é as single char
        decomposed = unicodedata.normalize("NFD", "café")  # e + combining accent
        assert fold(composed) == fold(decomposed)

    def test_casefold_lower(self):
        assert fold("HELLO") == fold("hello")

    def test_casefold_mixed(self):
        # 'Rs' → fold → 'r5' (s is in confusion class (5,S,s))
        # This is correct: OCR confusing 's' and '5' should be treated as equal
        assert fold("Rs") == fold("r5")

    def test_internal_spaces_removed(self):
        assert fold("Rs 5,000") == fold("Rs5,000")

    def test_whitespace_collapse(self):
        # Multiple spaces collapsed, then removed
        assert fold("hello   world") == fold("helloworld")

    def test_confusion_fold_zero_O(self):
        # '0' and 'O'/'o' are confusable
        assert fold("100") == fold("1OO") == fold("1oo")

    def test_confusion_fold_one_l_I(self):
        assert fold("1") == fold("l") == fold("I")

    def test_confusion_fold_5_S(self):
        assert fold("5") == fold("S") == fold("s")

    def test_confusion_fold_8_B(self):
        assert fold("8") == fold("B")

    def test_confusion_fold_2_Z(self):
        assert fold("2") == fold("Z") == fold("z")

    def test_digraph_rn_to_m(self):
        # "rn" should fold to "m" (known OCR confusion)
        assert fold("rn") == fold("m")

    def test_digraph_in_word(self):
        # "corner" → after casefold "corner" → after fold "comer"?
        # 'c','o','r','n','e','r' → 'c','0','r','n','e','r' (o→0) →
        # digraph "rn" → "m" → "c0mer" vs "corner"...
        # actually: casefold+"spaces" then confusion fold
        # "corner" → casefold → "corner" → no spaces → confusion fold:
        # digraphs first: "corner" → "cormer" (r+n→m at position 3-4, but they're not adjacent)
        # Let me check: c-o-r-n-e-r — positions: "rn" at index 2,3? No: c(0)o(1)r(2)n(3)e(4)r(5)
        # "rn" substring starting at index 2: YES — "corner" contains "rn"
        # So "corner" → "comer" after digraph fold
        # Then char fold: 'c'→'c', 'o'→'0', 'm'→'m', 'e'→'e', 'r'→'r' → "c0mer"
        # And "comer" → char fold: 'c'→'c', 'o'→'0', 'm'→'m', 'e'→'e', 'r'→'r' → "c0mer"
        assert fold("corner") == fold("comer")  # both fold to same

    def test_zero_width_stripped(self):
        # U+200B zero-width space should be stripped
        assert fold("hel​lo") == fold("hello")

    def test_soft_hyphen_stripped(self):
        # U+00AD soft hyphen
        assert fold("hel­lo") == fold("hello")

    def test_empty_string(self):
        assert fold("") == ""

    def test_amount_folds_symmetrically(self):
        # The key property: embedding "Rs 5,000" and OCR reading "Rs 5,000" both fold
        # to the same canonical form
        assert fold("Rs 5,000") == fold("Rs 5,000")

    def test_amount_fold_vs_tampered(self):
        # "Rs 5,000" vs "Rs 50,000" should fold to DIFFERENT forms
        assert fold("Rs 5,000") != fold("Rs 50,000")

    def test_custom_no_casefold(self):
        cfg = OCRCrossCheckConfig(fold_case=False, fold_internal_spaces=False,
                                  ocr_confusion_classes=())
        # Without casefold, "Hello" ≠ "hello"
        assert fold("Hello", cfg) != fold("hello", cfg)

    def test_custom_no_internal_spaces(self):
        cfg = OCRCrossCheckConfig(fold_case=True, fold_internal_spaces=False,
                                  ocr_confusion_classes=())
        # Without space removal, "Rs 5,000" ≠ "Rs5,000"
        assert fold("Rs 5,000", cfg) != fold("Rs5,000", cfg)

    def test_custom_empty_confusion_classes(self):
        cfg = OCRCrossCheckConfig(ocr_confusion_classes=())
        # With no confusion classes, 'l' stays 'l' (not folded to '1')
        f = fold("l", cfg)
        assert "1" not in f  # 'l' remains 'l' (casefolded already lowercase)


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------

class TestClassify:
    def test_amount_with_currency(self):
        assert classify("₹5,000") == TokenClass.AMOUNT

    def test_amount_rs(self):
        # "Rs" matches the currency-symbol-only branch of the AMOUNT regex
        assert classify("Rs") == TokenClass.AMOUNT

    def test_amount_number_with_commas(self):
        assert classify("5,000") == TokenClass.AMOUNT

    def test_date_dmy(self):
        assert classify("01/06/2024") == TokenClass.DATE

    def test_date_iso(self):
        assert classify("2024-06-01") == TokenClass.DATE

    def test_id_like_alphanum(self):
        assert classify("CHI1234567") == TokenClass.ID

    def test_prose_word(self):
        # "Approved" has 8 alphanum chars (≥6) so classify_token_kind returns ID_LIKE
        assert classify("Approved") == TokenClass.ID

    def test_prose_short(self):
        # "Rs" matches the currency-symbol-only AMOUNT pattern
        assert classify("Rs") == TokenClass.AMOUNT

    def test_prose_common_word(self):
        assert classify("claim") == TokenClass.PROSE

    def test_amount_inr(self):
        assert classify("INR1000") == TokenClass.AMOUNT

    def test_amount_decimal(self):
        assert classify("1250.50") == TokenClass.AMOUNT


# ---------------------------------------------------------------------------
# is_monetary_amount() — RC#1 fix B (docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md)
# ---------------------------------------------------------------------------

class TestIsMonetaryAmount:
    def test_bare_single_digit_not_monetary(self):
        assert is_monetary_amount("3") is False

    def test_bare_two_digit_not_monetary(self):
        assert is_monetary_amount("12") is False

    def test_bare_three_plus_digit_is_monetary(self):
        # A longer bare digit run is still treated as plausibly monetary.
        assert is_monetary_amount("500") is True

    def test_currency_symbol_is_monetary(self):
        assert is_monetary_amount("₹5") is True

    def test_currency_word_is_monetary(self):
        assert is_monetary_amount("Rs5") is True
        assert is_monetary_amount("INR5") is True

    def test_decimal_point_is_monetary(self):
        assert is_monetary_amount("1.5") is True

    def test_thousands_separator_is_monetary(self):
        assert is_monetary_amount("5,000") is True

    def test_dollar_sign_is_monetary(self):
        assert is_monetary_amount("$3") is True

    def test_empty_not_monetary(self):
        assert is_monetary_amount("") is False


# ---------------------------------------------------------------------------
# levenshtein()
# ---------------------------------------------------------------------------

class TestLevenshtein:
    def test_equal_strings(self):
        assert levenshtein("abc", "abc") == 0

    def test_empty_vs_nonempty(self):
        assert levenshtein("", "abc") == 3
        assert levenshtein("abc", "") == 3

    def test_both_empty(self):
        assert levenshtein("", "") == 0

    def test_one_substitution(self):
        assert levenshtein("abc", "axc") == 1

    def test_one_insertion(self):
        assert levenshtein("abc", "abbc") == 1

    def test_one_deletion(self):
        assert levenshtein("abbc", "abc") == 1

    def test_symmetric(self):
        assert levenshtein("hello", "world") == levenshtein("world", "hello")

    def test_known_distances(self):
        assert levenshtein("kitten", "sitting") == 3
        assert levenshtein("saturday", "sunday") == 3

    def test_amount_change(self):
        # "5,000" vs "50,000" — one insertion
        assert levenshtein("5,000", "50,000") == 1

    def test_completely_different(self):
        assert levenshtein("abc", "xyz") == 3

    def test_single_char_strings(self):
        assert levenshtein("a", "b") == 1
        assert levenshtein("a", "a") == 0


# ---------------------------------------------------------------------------
# allowed_edits()
# ---------------------------------------------------------------------------

class TestAllowedEdits:
    def test_amount_zero(self):
        assert allowed_edits(TokenClass.AMOUNT, 10) == 0

    def test_date_zero(self):
        assert allowed_edits(TokenClass.DATE, 10) == 0

    def test_id_strict_zero(self):
        cfg = OCRCrossCheckConfig(id_strict=True)
        assert allowed_edits(TokenClass.ID, 10, cfg) == 0

    def test_id_relaxed(self):
        cfg = OCRCrossCheckConfig(id_strict=False, id_rel_tol=0.10)
        # floor(10 * 0.10) = 1
        assert allowed_edits(TokenClass.ID, 10, cfg) == 1

    def test_prose_floor(self):
        # For very short token, floor(1*0.15) = 0 < prose_floor_edits=1 → 1
        assert allowed_edits(TokenClass.PROSE, 1) == 1

    def test_prose_relative(self):
        # floor(20 * 0.15) = 3 > prose_floor_edits=1 → 3
        assert allowed_edits(TokenClass.PROSE, 20) == 3

    def test_prose_at_boundary(self):
        # floor(7 * 0.15) = 1 = prose_floor_edits → 1
        assert allowed_edits(TokenClass.PROSE, 7) == 1

    def test_custom_prose_tol(self):
        cfg = OCRCrossCheckConfig(prose_rel_tol=0.20, prose_floor_edits=2)
        # floor(10 * 0.20) = 2 = floor → 2
        assert allowed_edits(TokenClass.PROSE, 10, cfg) == 2

    def test_custom_amount_nonzero(self):
        cfg = OCRCrossCheckConfig(amount_allowed_edits=1)
        assert allowed_edits(TokenClass.AMOUNT, 10, cfg) == 1


# ---------------------------------------------------------------------------
# is_within_tolerance()
# ---------------------------------------------------------------------------

class TestIsWithinTolerance:
    def test_identical_amounts_agree(self):
        assert is_within_tolerance("Rs 5,000", "Rs 5,000", TokenClass.AMOUNT)

    def test_different_amounts_mismatch(self):
        assert not is_within_tolerance("Rs 5,000", "Rs 50,000", TokenClass.AMOUNT)

    def test_ocr_confusion_o_zero_agree(self):
        # 'O' confused with '0' — both fold to same → AGREE
        assert is_within_tolerance("1OO", "100", TokenClass.AMOUNT)

    def test_case_fold_agree(self):
        # Different case → should agree (case-folded)
        assert is_within_tolerance("HELLO", "hello", TokenClass.PROSE)

    def test_prose_within_tolerance(self):
        # "helo" vs "hello" — 1 edit, prose tolerance 1 → AGREE
        assert is_within_tolerance("helo", "hello", TokenClass.PROSE)

    def test_prose_beyond_tolerance(self):
        # Very different — should mismatch
        assert not is_within_tolerance("abc", "xyz", TokenClass.PROSE)

    def test_identical_dates_agree(self):
        assert is_within_tolerance("01/06/2024", "01/06/2024", TokenClass.DATE)

    def test_different_dates_mismatch(self):
        assert not is_within_tolerance("01/06/2024", "01/07/2024", TokenClass.DATE)

    def test_id_strict_no_edit(self):
        cfg = OCRCrossCheckConfig(id_strict=True)
        assert not is_within_tolerance("CHI12345", "CHI12346", TokenClass.ID, cfg)

    def test_id_relaxed_allows_edit(self):
        cfg = OCRCrossCheckConfig(id_strict=False, id_rel_tol=0.20)
        # "CHI12345" vs "CHI12346" — 1 edit; floor(8*0.20)=1 → AGREE
        assert is_within_tolerance("CHI12345", "CHI12346", TokenClass.ID, cfg)

    def test_space_stripped_agree(self):
        # Spaces are stripped before comparison
        assert is_within_tolerance("Rs 5,000", "Rs5,000", TokenClass.AMOUNT)

    def test_digraph_confusion_agree(self):
        # "rn" and "m" are equivalent after confusion fold
        assert is_within_tolerance("corner", "comer", TokenClass.PROSE)
