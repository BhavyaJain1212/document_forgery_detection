"""Unit tests for font-name parsing and comparison helpers (fonts.py)."""

from __future__ import annotations

from pdf_forgery.font_forensics.fonts import (
    parse_font_identity,
    is_style_variant,
    is_substitution,
    same_base_different_subset,
)


# --------------------------------------------------------------------------- #
# parse_font_identity
# --------------------------------------------------------------------------- #

def test_parse_subset_font():
    fid = parse_font_identity("ABCDEF+Arial")
    assert fid.subset_tag == "ABCDEF"
    assert fid.base == "Arial"
    assert fid.family == "Arial"
    assert fid.is_subset is True


def test_parse_plain_standard_font():
    fid = parse_font_identity("Helvetica")
    assert fid.subset_tag is None
    assert fid.base == "Helvetica"
    assert fid.family == "Helvetica"
    assert fid.is_subset is False


def test_parse_strips_style_suffix_to_family():
    assert parse_font_identity("Helvetica-Bold").family == "Helvetica"
    assert parse_font_identity("Arial,BoldItalic").family == "Arial"
    assert parse_font_identity("ABCDEF+TimesNewRoman-Italic").family == "TimesNewRoman"


def test_parse_keeps_nonstyle_suffix_in_family():
    # 'CondensedExtra' is not a known style word component handled here, but
    # 'Condensed' is; the non-style remainder is preserved.
    assert parse_font_identity("Helvetica-Neue").family == "Helvetica-Neue"


def test_family_root_strips_vendor_suffix_glued():
    assert parse_font_identity("ArialMT").family == "Arial"
    assert parse_font_identity("TimesNewRomanPSMT").family == "TimesNewRoman"


def test_family_root_strips_compound_style_vendor():
    assert parse_font_identity("Arial-BoldMT").family == "Arial"
    assert parse_font_identity("CourierNewPS-BoldMT").family == "CourierNew"


def test_family_root_preserves_real_nonstyle_suffix():
    assert parse_font_identity("Helvetica-Neue").family == "Helvetica-Neue"
    assert is_substitution(
        parse_font_identity("CourierNewPS-BoldMT"),
        parse_font_identity("ArialMT"),
    ) is True


def test_parse_placeholder_names():
    assert parse_font_identity("unknown").is_placeholder is True
    assert parse_font_identity("").is_placeholder is True
    assert parse_font_identity(None).is_placeholder is True


def test_parse_five_letter_prefix_is_not_a_subset_tag():
    # Subset tags are exactly six uppercase letters; 'ABCDE+' must not parse.
    fid = parse_font_identity("ABCDE+Arial")
    assert fid.subset_tag is None
    assert fid.base == "ABCDE+Arial"


# --------------------------------------------------------------------------- #
# same_base_different_subset (the re-embedding fingerprint)
# --------------------------------------------------------------------------- #

def test_same_base_different_subset_true():
    a = parse_font_identity("ABCDEF+Helvetica")
    b = parse_font_identity("GHIJKL+Helvetica")
    assert same_base_different_subset(a, b) is True


def test_same_base_same_subset_false():
    a = parse_font_identity("ABCDEF+Helvetica")
    assert same_base_different_subset(a, a) is False


def test_different_base_subset_false():
    a = parse_font_identity("ABCDEF+Helvetica")
    b = parse_font_identity("GHIJKL+Arial")
    assert same_base_different_subset(a, b) is False


def test_one_side_not_subset_false():
    a = parse_font_identity("ABCDEF+Helvetica")
    b = parse_font_identity("Helvetica")
    assert same_base_different_subset(a, b) is False


# --------------------------------------------------------------------------- #
# is_style_variant / is_substitution
# --------------------------------------------------------------------------- #

def test_bold_is_style_variant_not_substitution():
    a = parse_font_identity("Helvetica")
    b = parse_font_identity("Helvetica-Bold")
    assert is_style_variant(a, b) is True
    assert is_substitution(a, b) is False


def test_arialmt_bold_is_style_variant_not_substitution():
    a = parse_font_identity("Arial-BoldMT")
    b = parse_font_identity("ArialMT")
    assert is_style_variant(a, b) is True
    assert is_substitution(a, b) is False


def test_different_family_is_substitution():
    a = parse_font_identity("Helvetica")
    b = parse_font_identity("Times-Roman")
    assert is_style_variant(a, b) is False
    assert is_substitution(a, b) is True


def test_identical_font_is_style_variant_not_substitution():
    a = parse_font_identity("Helvetica")
    assert is_style_variant(a, a) is True
    assert is_substitution(a, a) is False


def test_placeholder_never_substitution():
    a = parse_font_identity("unknown")
    b = parse_font_identity("Helvetica")
    assert is_substitution(a, b) is False
