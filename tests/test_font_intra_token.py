"""Unit tests for intra-token font-mixing detection (FIX 1).

Each test drives :func:`detect_findings` with hand-built glyph rows so a single
inserted/edited glyph in a foreign font — masked by the per-token dominant-font
view — is exercised in isolation, independent of pdfminer.
"""

from __future__ import annotations

import pytest

from pdf_forgery.font_forensics.config import FontConfig
from pdf_forgery.font_forensics.detect import detect_findings
from pdf_forgery.font_forensics.models import (
    ConfidenceTier,
    FontFindingKind,
    Glyph,
    HighValueKind,
)

_W = 8.0
_SIZE = 14.0

HELV = "Helvetica"
TIMES = "Times-Roman"
COURIER = "Courier"
SUB_A = "ABCDEF+Helvetica"
SUB_B = "GHIJKL+Helvetica"  # same base, different subset tag


def _uniform(text: str, font: str):
    """A token spec: every char in one font."""
    return [(ch, font) for ch in text]


def _with_foreign(text: str, font: str, foreign_font: str, index: int):
    """A token spec where the char at *index* is in *foreign_font*."""
    spec = _uniform(text, font)
    spec[index] = (text[index], foreign_font)
    return spec


def build_line(token_specs, *, page=0, y0=700.0):
    """Build a glyph row from ``[[(char, font), ...], ...]`` token specs."""
    glyphs: list[Glyph] = []
    x = 72.0
    for i, spec in enumerate(token_specs):
        if i > 0:
            sep_font = spec[0][1]
            glyphs.append(Glyph(" ", sep_font, _SIZE, x, y0, x + _W, y0 + _SIZE, page))
            x += _W
        for ch, font in spec:
            glyphs.append(Glyph(ch, font, _SIZE, x, y0, x + _W, y0 + _SIZE, page))
            x += _W
    return glyphs


def _intra(findings):
    return [f for f in findings if f.kind is FontFindingKind.INTRA_TOKEN_FONT_MIX]


# --------------------------------------------------------------------------- #
# Foreign FAMILY glyph inside an amount token
# --------------------------------------------------------------------------- #

def test_foreign_family_glyph_in_amount_high():
    # "Total:" label + "50,000" whose '5' is in a different family.
    line = build_line([_uniform("Total:", HELV), _with_foreign("50,000", HELV, TIMES, 0)])
    findings, _ = detect_findings(line)
    intra = _intra(findings)
    assert len(intra) == 1
    f = intra[0]
    assert f.tier is ConfidenceTier.HIGH
    assert f.token == "50,000"
    assert f.high_value is HighValueKind.AMOUNT
    assert f.token_font == HELV          # majority
    assert f.minority_font == TIMES      # minority
    assert f.suspicious_text == "5"
    assert f.suspicious_glyph_indexes == (0,)
    assert len(f.suspicious_bboxes) == 1


def test_acrobat_shape_inserted_zero_high():
    # Mirrors the Acrobat case: 7 Calibri digits + one foreign-family '0'.
    line = build_line(
        [_uniform("Amount", "YWNRZS+Calibri"),
         _with_foreign("18071.23", "YWNRZS+Calibri", "SUMSRI+SourceSansPro-Regular", 2)]
    )
    findings, _ = detect_findings(line)
    (f,) = _intra(findings)
    assert f.tier is ConfidenceTier.HIGH
    assert f.token == "18071.23"
    assert f.suspicious_text == "0"
    assert f.suspicious_glyph_indexes == (2,)
    assert f.token_font == "YWNRZS+Calibri"
    assert f.minority_font == "SUMSRI+SourceSansPro-Regular"


# --------------------------------------------------------------------------- #
# Foreign SUBSET glyph inside a date token
# --------------------------------------------------------------------------- #

def test_foreign_subset_glyph_in_date_high():
    line = build_line(
        [_uniform("Paid", SUB_A), _with_foreign("12/05/2024", SUB_A, SUB_B, 3)]
    )
    findings, _ = detect_findings(line)
    (f,) = _intra(findings)
    assert f.tier is ConfidenceTier.HIGH
    assert f.token == "12/05/2024"
    assert f.high_value is HighValueKind.DATE
    assert f.minority_font == SUB_B
    assert "subset" in f.reason


# --------------------------------------------------------------------------- #
# Multiple suspicious glyphs in one token
# --------------------------------------------------------------------------- #

def test_multiple_suspicious_glyphs_one_token():
    spec = _uniform("50,000", HELV)
    spec[0] = ("5", TIMES)
    spec[5] = ("0", TIMES)
    line = build_line([_uniform("Total", HELV), spec])
    findings, _ = detect_findings(line)
    (f,) = _intra(findings)
    assert f.tier is ConfidenceTier.HIGH
    assert f.suspicious_glyph_indexes == (0, 5)
    assert f.suspicious_text == "50"
    assert len(f.suspicious_bboxes) == 2


# --------------------------------------------------------------------------- #
# Foreign glyph at token start / middle / end
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("index", [0, 3, 5])
def test_foreign_glyph_position(index):
    line = build_line([_uniform("Total", HELV), _with_foreign("50,000", HELV, TIMES, index)])
    findings, _ = detect_findings(line)
    (f,) = _intra(findings)
    assert f.tier is ConfidenceTier.HIGH
    assert f.suspicious_glyph_indexes == (index,)


# --------------------------------------------------------------------------- #
# Whole-token bold / italic must NOT flag (no in-token mixing)
# --------------------------------------------------------------------------- #

def test_whole_token_bold_not_flagged():
    line = build_line([_uniform("Total:", HELV), _uniform("50,000", "Helvetica-Bold")])
    findings, _ = detect_findings(line)
    assert _intra(findings) == []


def test_whole_token_italic_not_flagged():
    line = build_line([_uniform("Total:", HELV), _uniform("50,000", "Helvetica-Oblique")])
    findings, _ = detect_findings(line)
    assert _intra(findings) == []


def test_intra_token_style_variant_mix_not_flagged():
    # A token mixing Helvetica + Helvetica-Bold is emphasis, not a substitution.
    spec = _uniform("50,000", HELV)
    spec[0] = ("5", "Helvetica-Bold")
    line = build_line([_uniform("Total", HELV), spec])
    findings, _ = detect_findings(line)
    assert _intra(findings) == []


# --------------------------------------------------------------------------- #
# Unknown / placeholder font must NOT independently produce HIGH
# --------------------------------------------------------------------------- #

def test_placeholder_minority_not_flagged():
    line = build_line([_uniform("Total", HELV), _with_foreign("50,000", HELV, "", 0)])
    findings, _ = detect_findings(line)
    assert _intra(findings) == []
    assert all(f.tier is not ConfidenceTier.HIGH for f in findings)


def test_placeholder_majority_not_flagged():
    # Majority font unnamed -> cannot anchor a confident call.
    spec = _uniform("50,000", "")
    spec[0] = ("5", TIMES)
    line = build_line([_uniform("Total", HELV), spec])
    findings, _ = detect_findings(line)
    assert _intra(findings) == []


# --------------------------------------------------------------------------- #
# Dedup: the coarser token-level detector already flagged the token
# --------------------------------------------------------------------------- #

def test_dedup_against_token_level_detector():
    # Context is Times-Roman; the amount token is MOSTLY Helvetica (so its
    # dominant font differs from the line -> token-level substitution HIGH) with
    # ONE Courier glyph (which would otherwise trip the intra-token detector).
    line = build_line(
        [_uniform("Paid", TIMES), _uniform("total", TIMES), _uniform("here", TIMES),
         _with_foreign("50,000", HELV, COURIER, 0)]
    )
    findings, _ = detect_findings(line)
    # Exactly one finding for the token: the token-level substitution, NOT a
    # duplicate intra-token finding.
    amount_findings = [f for f in findings if f.token == "50,000"]
    assert len(amount_findings) == 1
    assert amount_findings[0].kind is FontFindingKind.HIGH_VALUE_SUBSTITUTION
    assert _intra(findings) == []


# --------------------------------------------------------------------------- #
# Non-high-value (prose) token with an intra-token switch -> MEDIUM
# --------------------------------------------------------------------------- #

def test_prose_intra_token_mix_medium():
    line = build_line([_uniform("hello", HELV), _with_foreign("word", HELV, TIMES, 0)])
    findings, _ = detect_findings(line)
    (f,) = _intra(findings)
    assert f.tier is ConfidenceTier.MEDIUM
    assert f.high_value is None


def test_prose_intra_token_mix_disabled():
    cfg = FontConfig(flag_intra_token_mix_prose=False)
    line = build_line([_uniform("hello", HELV), _with_foreign("word", HELV, TIMES, 0)])
    findings, _ = detect_findings(line, cfg)
    assert _intra(findings) == []


# --------------------------------------------------------------------------- #
# Base-rate guard: pervasive intra-token mixing is downgraded
# --------------------------------------------------------------------------- #

def _pervasive_doc():
    """10 lines, each with one intra-token-mixed token; one of them an amount."""
    glyphs: list[Glyph] = []
    for i in range(9):
        glyphs += build_line([_with_foreign("wordx", HELV, TIMES, 0)], y0=700.0 - i * 30)
    # The amount line (would be HIGH absent the guard).
    glyphs += build_line([_with_foreign("50,000", HELV, TIMES, 0)], y0=700.0 - 9 * 30)
    return glyphs


def test_pervasive_mixing_downgrades_amount_to_medium():
    findings, _ = detect_findings(_pervasive_doc())
    amount = [f for f in _intra(findings) if f.token == "50,000"]
    assert len(amount) == 1
    assert amount[0].tier is ConfidenceTier.MEDIUM  # downgraded from HIGH
    assert "pervasive" in amount[0].reason
    # Prose intra-token mixes are dropped entirely under the guard.
    assert all(f.token == "50,000" for f in _intra(findings))


def test_non_pervasive_amount_stays_high():
    # The same amount line in a small, otherwise-clean document stays HIGH.
    glyphs = build_line([_uniform("Total", HELV), _with_foreign("50,000", HELV, TIMES, 0)])
    findings, _ = detect_findings(glyphs)
    (f,) = _intra(findings)
    assert f.tier is ConfidenceTier.HIGH
