"""Unit + regression tests for the glyph-based extraction fallback (FIX 2).

The fallback only engages when the PRIMARY (container-level) text extractor
looks suspiciously incomplete. These tests cover the incompleteness heuristic in
both directions, the reconstructed page text, and the regression guarantee that
files where primary extraction already works are unchanged.
"""

from __future__ import annotations

from pdf_forgery.revision_recovery.analyze import analyze_path
from pdf_forgery.revision_recovery.config import Config
from pdf_forgery.revision_recovery.extract.glyph_text import (
    glyph_page_texts,
    looks_incomplete,
)
from pdf_forgery.revision_recovery.extract.normalize import normalize
from pdf_forgery.revision_recovery.extract.text import extract_text_per_page
from pdf_forgery.revision_recovery.models import ConfidenceTier


# --------------------------------------------------------------------------- #
# glyph_page_texts
# --------------------------------------------------------------------------- #

def test_glyph_page_texts_recovers_text(forged_pdf):
    raw = forged_pdf.read_bytes()
    pages = glyph_page_texts(raw)
    assert pages and any("amount" in p.lower() for p in pages)


def test_glyph_page_texts_garbage_is_empty():
    assert glyph_page_texts(b"%PDF-1.4 not a pdf") == []


# --------------------------------------------------------------------------- #
# looks_incomplete heuristic
# --------------------------------------------------------------------------- #

def test_looks_incomplete_true_when_primary_is_a_fragment(forged_pdf):
    raw = forged_pdf.read_bytes()
    # A primary extraction that captured almost nothing vs the glyph path.
    assert looks_incomplete(["x"], raw) is True


def test_looks_incomplete_false_when_primary_matches(forged_pdf):
    raw = forged_pdf.read_bytes()
    primary = [normalize(p) for p in extract_text_per_page(raw)]
    # Primary works on this generated PDF -> not deemed incomplete.
    assert looks_incomplete(primary, raw) is False


def test_looks_incomplete_false_on_empty_glyphs():
    assert looks_incomplete(["x"], b"%PDF-1.4 junk") is False


def test_looks_incomplete_detects_missing_high_value_token(forged_pdf):
    raw = forged_pdf.read_bytes()
    glyph_pages = [normalize(p) for p in glyph_page_texts(raw)]
    # Same character volume as the glyph path, but strip the amounts: the
    # missing high-value tokens alone should flag incompleteness.
    scrubbed = [
        " ".join(t for t in page.split() if not any(c.isdigit() for c in t))
        for page in glyph_pages
    ]
    # Pad so the char-ratio test does not fire; only the high-value gap remains.
    scrubbed = scrubbed + ["filler " * 200]
    assert looks_incomplete(scrubbed, raw) is True


# --------------------------------------------------------------------------- #
# Regression guard: a PDF where primary extraction already works is unchanged
# --------------------------------------------------------------------------- #

def test_forged_fixture_unchanged_with_and_without_fallback(forged_pdf):
    enabled = analyze_path(forged_pdf, Config(enable_glyph_fallback=True))
    disabled = analyze_path(forged_pdf, Config(enable_glyph_fallback=False))
    assert enabled.scoring.tier is disabled.scoring.tier is ConfidenceTier.HIGH
    assert enabled.scoring.score == disabled.scoring.score
    # Identical findings (fallback never engaged: primary already substantive).
    assert [f.summary for f in enabled.findings] == [f.summary for f in disabled.findings]
    assert not any("fallback" in n for n in enabled.notes)


def test_clean_fixture_stays_inconclusive(clean_pdf):
    report = analyze_path(clean_pdf)
    assert report.scoring.tier is ConfidenceTier.INCONCLUSIVE
    assert report.findings == ()
