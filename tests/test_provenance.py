"""provenance_metadata: producer/date/ID checks; never HIGH; real samples."""

from __future__ import annotations

from pdf_forgery.core import ConfidenceTier
from pdf_forgery.provenance_metadata import (
    ProvenanceConfig,
    analyze_path,
    detect,
    mod_after_creation,
    parse_pdf_date,
    score,
)
from pdf_forgery.provenance_metadata.detect import Metadata
from pdf_forgery.provenance_metadata.models import ProvenanceFindingKind


def _meta(**kw) -> Metadata:
    base = dict(
        producer=None, creator=None, creation_date=None, mod_date=None,
        xmp_producer=None, id_pair=None,
    )
    base.update(kw)
    return Metadata(**base)


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #

def test_parse_pdf_date_with_timezone():
    dt = parse_pdf_date("D:20260603162935+05'30'")
    assert dt is not None and dt.year == 2026 and dt.month == 6


def test_mod_after_creation():
    assert mod_after_creation("D:20260603162935+05'30'", "D:20260612094415+02'00'") is True
    assert mod_after_creation("D:20260612", "D:20260603") is False
    assert mod_after_creation(None, "D:20260612") is False


# --------------------------------------------------------------------------- #
# Producer / creator fingerprinting
# --------------------------------------------------------------------------- #

def test_named_web_editor_producer_flagged():
    findings = detect(_meta(producer="Sejda 1.2.3 desktop"))
    kinds = {f.kind for f in findings}
    assert ProvenanceFindingKind.WEB_EDITOR_PRODUCER in kinds


def test_version_only_producer_is_the_fingerprint():
    # The bare "3.0.35 (5.1.21)" form is flagged even without a brand name.
    findings = detect(_meta(producer="3.0.35 (5.1.21)"))
    kinds = {f.kind for f in findings}
    assert ProvenanceFindingKind.VERSION_ONLY_PRODUCER in kinds


def test_browser_creator_flagged():
    findings = detect(_meta(creator="Mozilla Firefox 151.0.2"))
    assert any(f.kind is ProvenanceFindingKind.BROWSER_CREATOR for f in findings)


def test_moddate_after_creation_flagged():
    findings = detect(_meta(
        creation_date="D:20260603162935+05'30'", mod_date="D:20260612094415+02'00'"
    ))
    assert any(f.kind is ProvenanceFindingKind.MODDATE_AFTER_CREATION for f in findings)


def test_xmp_info_producer_mismatch_flagged():
    findings = detect(_meta(producer="BillingSystem 4.0", xmp_producer="Sejda"))
    assert any(
        f.kind is ProvenanceFindingKind.XMP_INFO_PRODUCER_MISMATCH for f in findings
    )


def test_id_halves_mismatch_flagged():
    findings = detect(_meta(id_pair=("aabb", "ccdd")))
    assert any(f.kind is ProvenanceFindingKind.ID_HALVES_MISMATCH for f in findings)


def test_composite_footprint_combines_signals():
    findings = detect(_meta(
        producer="3.0.35 (5.1.21)", creator="Mozilla Firefox 151.0.2",
        creation_date="D:20260603162935+05'30'", mod_date="D:20260612094415+02'00'",
    ))
    assert any(
        f.kind is ProvenanceFindingKind.COMPOSITE_EDIT_FOOTPRINT for f in findings
    )


# --------------------------------------------------------------------------- #
# Scoring: provenance NEVER reaches HIGH (corroborator only)
# --------------------------------------------------------------------------- #

def test_metadata_never_high():
    cfg = ProvenanceConfig()
    findings = detect(_meta(
        producer="Sejda", creator="Mozilla Firefox",
        creation_date="D:20260603", mod_date="D:20260612",
        xmp_producer="Other", id_pair=("aa", "bb"),
    ), cfg)
    tier, sc, _ = score(findings, metadata_readable=True, config=cfg)
    assert tier in (ConfidenceTier.LOW, ConfidenceTier.MEDIUM)
    assert tier is not ConfidenceTier.HIGH
    assert sc is not None and sc <= cfg.medium_ceiling


def test_clean_metadata_is_low():
    findings = detect(_meta(
        producer="Acme Hospital Billing System 7.2",
        creation_date="D:20260603", mod_date="D:20260603",
    ))
    tier, sc, _ = score(findings, metadata_readable=True)
    assert tier is ConfidenceTier.LOW


def test_unreadable_metadata_inconclusive():
    tier, sc, _ = score([], metadata_readable=False)
    assert tier is ConfidenceTier.INCONCLUSIVE
    assert sc is None


# --------------------------------------------------------------------------- #
# Real samples: both Sejda files carry the footprint, capped at MEDIUM.
# --------------------------------------------------------------------------- #

def test_sejda_tampered_provenance_supports(sejda_tampered_pdf):
    report = analyze_path(sejda_tampered_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.MEDIUM
    assert report.tier is not ConfidenceTier.HIGH
    assert any(
        f.kind is ProvenanceFindingKind.COMPOSITE_EDIT_FOOTPRINT
        for f in report.findings
    )
    assert report.producer is not None


def test_microsoft_sample_provenance_also_flagged(microsoft_clean_pdf):
    # Same Sejda footprint as the tampered file -> truthfully MEDIUM, never HIGH.
    report = analyze_path(microsoft_clean_pdf)
    assert report.ok is True
    assert report.tier is ConfidenceTier.MEDIUM
