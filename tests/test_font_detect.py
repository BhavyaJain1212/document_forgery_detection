"""Unit tests for glyph grouping, the detectors, and scoring (synthetic glyphs).

These drive the detection logic directly with hand-built :class:`Glyph` rows so
each tier and rule is exercised in isolation, independent of pdfminer.
"""

from __future__ import annotations

from pdf_forgery.font_forensics.config import FontConfig
from pdf_forgery.font_forensics.detect import detect_findings
from pdf_forgery.font_forensics.extract import (
    distinct_fonts,
    dominant_font,
    group_lines,
)
from pdf_forgery.font_forensics.models import (
    ConfidenceTier,
    FontFindingKind,
    Glyph,
    HighValueKind,
)
from pdf_forgery.font_forensics.scoring import score_findings


# --------------------------------------------------------------------------- #
# Synthetic-glyph builder: lay tokens out left-to-right with spaces between.
# --------------------------------------------------------------------------- #

_W = 8.0
_SIZE = 14.0


def line_glyphs(spec, *, page=0, y0=700.0):
    """Build a left-to-right glyph row from ``[(text, fontname), ...]`` tokens."""
    glyphs: list[Glyph] = []
    x = 72.0
    for i, (text, font) in enumerate(spec):
        if i > 0:
            glyphs.append(Glyph(" ", font, _SIZE, x, y0, x + _W, y0 + _SIZE, page))
            x += _W
        for ch in text:
            glyphs.append(Glyph(ch, font, _SIZE, x, y0, x + _W, y0 + _SIZE, page))
            x += _W
    return glyphs


SUB_A = "ABCDEF+Helvetica"
SUB_B = "GHIJKL+Helvetica"


# --------------------------------------------------------------------------- #
# group_lines / dominant_font
# --------------------------------------------------------------------------- #

def test_group_lines_splits_by_baseline():
    g = line_glyphs([("Top", "Helvetica")], y0=700.0)
    g += line_glyphs([("Bottom", "Helvetica")], y0=600.0)
    lines = group_lines(g)
    assert len(lines) == 2
    # Top-to-bottom order.
    assert "".join(c.text for c in lines[0].glyphs).strip() == "Top"


def test_group_lines_tokenises_on_spaces():
    g = line_glyphs([("Approved", "Helvetica"), ("amount:", "Helvetica")])
    (line,) = group_lines(g)
    assert [t.text for t in line.tokens] == ["Approved", "amount:"]


def test_dominant_font_majority_and_determinism():
    g = line_glyphs([("aaaa", SUB_A), ("bb", SUB_B)])
    assert dominant_font(g) == SUB_A  # 4 glyphs vs 2
    assert distinct_fonts(g) == (SUB_A, SUB_B)


# --------------------------------------------------------------------------- #
# HIGH: high-value token breaks line context
# --------------------------------------------------------------------------- #

def test_high_subset_split_on_amount():
    g = line_glyphs([("Approved", SUB_A), ("amount:", SUB_A), ("50,000", SUB_B)])
    findings, _ = detect_findings(g)
    assert len(findings) == 1
    f = findings[0]
    assert f.tier is ConfidenceTier.HIGH
    assert f.kind is FontFindingKind.HIGH_VALUE_SUBSET_SPLIT
    assert f.token == "50,000"
    assert f.high_value is HighValueKind.AMOUNT
    assert f.token_font == SUB_B
    assert f.context_font == SUB_A
    assert set(f.conflicting_fonts) == {SUB_A, SUB_B}
    # bbox is the amount token's box, not the whole line.
    assert f.bbox[0] > 72.0


def test_high_substitution_on_amount():
    g = line_glyphs([("Approved", "Helvetica"), ("amount:", "Helvetica"), ("50,000", "Times-Roman")])
    findings, _ = detect_findings(g)
    assert len(findings) == 1
    assert findings[0].tier is ConfidenceTier.HIGH
    assert findings[0].kind is FontFindingKind.HIGH_VALUE_SUBSTITUTION


def test_high_on_date_token():
    # Short, non-ID context words so the only anomaly is the date in SUB_B; the
    # SUB_A context must dominate (>= the date's glyph count) to be the baseline.
    g = line_glyphs(
        [("Paid", SUB_A), ("on", SUB_A), ("this", SUB_A), ("very", SUB_A),
         ("day", SUB_A), ("12/05/2024", SUB_B)]
    )
    findings, _ = detect_findings(g)
    assert len(findings) == 1
    assert findings[0].tier is ConfidenceTier.HIGH
    assert findings[0].token == "12/05/2024"
    assert findings[0].high_value is HighValueKind.DATE


# --------------------------------------------------------------------------- #
# Benign: style variant (bold) on a high-value token is NOT flagged
# --------------------------------------------------------------------------- #

def test_bold_label_regular_amount_not_flagged():
    g = line_glyphs([("Total:", "Helvetica-Bold"), ("50,000", "Helvetica")])
    findings, _ = detect_findings(g)
    assert findings == []


def test_insufficient_context_not_flagged():
    # Amount first with only a 2-glyph context -> below min_context_glyphs.
    # Non-subset fonts so the intra-line subset-split rule cannot fire either.
    g = line_glyphs([("50,000", "Times-Roman"), ("ok", "Helvetica")])
    findings, _ = detect_findings(g)
    assert findings == []


# --------------------------------------------------------------------------- #
# MEDIUM: intra-line subset split NOT on a high-value token
# --------------------------------------------------------------------------- #

def test_medium_intra_line_subset_split_prose():
    g = line_glyphs([("the", SUB_A), ("net", SUB_A), ("sum", SUB_B)])
    findings, _ = detect_findings(g)
    assert len(findings) == 1
    f = findings[0]
    assert f.tier is ConfidenceTier.MEDIUM
    assert f.kind is FontFindingKind.INTRA_LINE_SUBSET_SPLIT
    assert f.token == "sum"
    assert f.high_value is None


# --------------------------------------------------------------------------- #
# MEDIUM: baseline deviation on a uniform line (no line context)
# --------------------------------------------------------------------------- #

def test_medium_baseline_deviation_uniform_line():
    # Body baseline is Helvetica (more glyphs); a lone amount line in Times.
    body = line_glyphs([("Care", "Helvetica"), ("Health", "Helvetica"), ("Insurance", "Helvetica")], y0=700.0)
    amount = line_glyphs([("50,000", "Times-Roman")], y0=660.0)
    findings, _ = detect_findings(body + amount)
    assert len(findings) == 1
    f = findings[0]
    assert f.tier is ConfidenceTier.MEDIUM
    assert f.kind is FontFindingKind.HIGH_VALUE_BASELINE_DEVIATION
    assert f.high_value is HighValueKind.AMOUNT


def test_baseline_deviation_skips_style_variant():
    body = line_glyphs([("Care", "Helvetica"), ("Health", "Helvetica"), ("Insurance", "Helvetica")], y0=700.0)
    amount = line_glyphs([("50,000", "Helvetica-Bold")], y0=660.0)  # just bold
    findings, _ = detect_findings(body + amount)
    assert findings == []


# --------------------------------------------------------------------------- #
# Scoring tiers
# --------------------------------------------------------------------------- #

def test_score_inconclusive_single_font():
    tier, score, reasons = score_findings([], distinct_font_count=1, comparable_glyphs=50)
    assert tier is ConfidenceTier.INCONCLUSIVE
    assert score is None


def test_score_inconclusive_too_few_glyphs():
    tier, score, _ = score_findings([], distinct_font_count=2, comparable_glyphs=1)
    assert tier is ConfidenceTier.INCONCLUSIVE


def test_score_low_multifont_no_findings():
    tier, score, _ = score_findings([], distinct_font_count=2, comparable_glyphs=50)
    assert tier is ConfidenceTier.LOW
    assert score == FontConfig().score_low_default


def test_score_high_amount_beats_medium():
    g = line_glyphs([("Approved", SUB_A), ("amount:", SUB_A), ("50,000", SUB_B)])
    findings, _ = detect_findings(g)
    tier, score, reasons = score_findings(findings, 2, 50)
    assert tier is ConfidenceTier.HIGH
    assert score == FontConfig().score_high_amount_date
    assert any("high-value" in r for r in reasons)
