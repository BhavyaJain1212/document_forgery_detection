"""Regression for the clean Microsoft page-4 ABN false positive.

This replaces the old module-level PyMuPDF mutation scratch script. Tests must
never create an incrementally edited PDF during collection or depend on the
current working directory.
"""

from pdf_forgery.core import ConfidenceTier
from pdf_forgery.font_forensics import analyze_path


def test_page4_abn_uniform_font_difference_is_clean(page4_microsoft_pdf):
    report = analyze_path(page4_microsoft_pdf)

    assert report.ok is True
    assert report.tier is ConfidenceTier.LOW
    assert report.score == 15
    assert report.findings == ()
