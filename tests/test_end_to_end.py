"""End-to-end tests: the full detector against the shipped fixtures.

Runs ``analyze_path`` (read raw bytes -> detect -> reconstruct -> diff -> score
-> findings) on the known-positive and known-negative PDFs produced by
``scripts/make_fixtures.py`` and asserts the spec's expected outcomes:

  * forged (incremental amount edit) -> HIGH, "high-value field altered",
    amount kind, exact before/after text, CONTENT object, correct page.
  * clean (single revision)          -> INCONCLUSIVE.

These tests are the canonical acceptance check for Stage 1.
"""

from __future__ import annotations

import json

import make_fixtures  # provided on sys.path by conftest

from pdf_forgery.cli import main
from pdf_forgery.revision_recovery import (
    ConfidenceTier,
    HighValueKind,
    ObjectChangeClass,
    analyze_path,
)


# --------------------------------------------------------------------------- #
# Known-positive: forged incremental update -> HIGH
# --------------------------------------------------------------------------- #

def test_forged_scores_high_with_high_value_tag(forged_pdf):
    report = analyze_path(forged_pdf)

    assert report.ok is True
    assert report.scoring is not None
    s = report.scoring

    # Two revisions recovered from the incremental update.
    assert report.revision_count == 2
    assert report.reconstruction_failures == 0

    # HIGH tier, top score band, amount detected.
    assert s.tier is ConfidenceTier.HIGH
    assert s.score == 95
    assert s.has_substantive_text_change is True
    assert s.has_high_value_change is True
    assert s.high_value_kind is HighValueKind.AMOUNT

    # The "high-value field altered" tag is present in the reasons.
    assert any("high-value field altered" in r for r in s.reasons)


def test_forged_finding_has_correct_before_after(forged_pdf):
    report = analyze_path(forged_pdf)

    # Exactly one flagged change: the amount edit on page 1.
    findings = [f for f in report.findings if f.token_changes]
    assert len(findings) == 1
    f = findings[0]

    assert f.from_revision == 0 and f.to_revision == 1
    assert f.page_index == 0  # page 1

    # The change maps to a CONTENT object and is flagged high-value (amount).
    assert ObjectChangeClass.CONTENT in f.object_classes
    assert f.object_ids  # at least one "<obj> <gen>" id
    assert f.is_high_value is True
    assert f.high_value_kind is HighValueKind.AMOUNT

    # Exact before -> after text. ORIGINAL/FORGED amounts are "Rs 5,000" /
    # "Rs 50,000"; "Rs" is unchanged so only the number token differs.
    assert "5,000" in make_fixtures.ORIGINAL_AMOUNT
    assert f.before_text == "5,000"
    assert f.after_text == "50,000"


# --------------------------------------------------------------------------- #
# Known-negative: clean single revision -> INCONCLUSIVE
# --------------------------------------------------------------------------- #

def test_clean_scores_inconclusive(clean_pdf):
    report = analyze_path(clean_pdf)

    assert report.ok is True
    assert report.scoring is not None
    s = report.scoring

    assert report.revision_count == 1
    assert s.tier is ConfidenceTier.INCONCLUSIVE
    assert s.score is None
    assert report.findings == ()
    assert any("single revision" in r.lower() for r in s.reasons)


# --------------------------------------------------------------------------- #
# CLI end-to-end over the fixtures
# --------------------------------------------------------------------------- #

def test_cli_summary_shows_before_after(forged_pdf, capsys):
    rc = main([str(forged_pdf)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "HIGH" in out
    assert "before: 5,000" in out
    assert "after:  50,000" in out


def test_cli_json_tiers_match(forged_pdf, clean_pdf, capsys):
    rc = main([str(forged_pdf), "--json", "-"])
    forged_json = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert forged_json["scoring"]["tier"] == "high"

    rc = main([str(clean_pdf), "--json", "-"])
    clean_json = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert clean_json["scoring"]["tier"] == "inconclusive"
    assert rc == 0  # verdict never affects the exit code
