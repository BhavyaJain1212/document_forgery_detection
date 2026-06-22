"""Stage 7.1 — aggregate() + PHI-scrub boundary + advisory (CPU-only logic)."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from pdf_forgery.aggregate import (
    AdvisoryFinding,
    AdvisoryInput,
    AdvisoryStage,
    AggregateConfig,
    FindingGroup,
    GroupExplanation,
    StubAdvisoryEngine,
    aggregate,
    assert_advisory_safe,
    generate_advisory,
    to_advisory_input,
)
from pdf_forgery.aggregate.models import BBox
from pdf_forgery.core.types import ConfidenceTier, Finding, StageResult
from pdf_forgery.font_forensics.models import FontFindingKind
from pdf_forgery.fusion import fuse
from pdf_forgery.invoice_arithmetic.models import RelationshipKind
from pdf_forgery.image_forensics import (
    ImageForensicsReport,
    RegionFinding,
    TamperRegion,
    report_to_stage_result,
)
from pdf_forgery.ocr_crosscheck.models import DivergenceType
from pdf_forgery.provenance_metadata.models import ProvenanceFindingKind
from pdf_forgery.revision_recovery.models import (
    BoxPt,
    Finding as RRFinding,
    FindingLocation,
    ObjectChangeClass,
)

REV = "revision_recovery"
FONT = "font_forensics"
ARITH = "invoice_arithmetic"
PROV = "provenance_metadata"
OCR = "ocr_crosscheck"
IMG = "image_forensics"


def _finding(stage: str, tier=ConfidenceTier.HIGH, **kw) -> Finding:
    defaults = dict(stage=stage, tier=tier, reason="x", page=0)
    defaults.update(kw)
    return Finding(**defaults)


def _stage_result(stage, tier, score=None, findings=(), payload=None, ok=True, error=None):
    return StageResult(
        stage=stage, tier=tier, score=score, findings=tuple(findings),
        summary="", reasons=(), notes=(), ok=ok, error=error, payload=payload,
    )


# --------------------------------------------------------------------------- #
# aggregate() roll-up matches fusion.fuse()
# --------------------------------------------------------------------------- #

def test_aggregate_headline_matches_fuse():
    results = [
        _stage_result(REV, ConfidenceTier.HIGH, 95, findings=[_finding(REV)]),
        _stage_result(FONT, ConfidenceTier.LOW, 15),
        _stage_result(PROV, ConfidenceTier.LOW, 0),
    ]
    fused = fuse(results)
    agg = aggregate(results)
    assert agg.tier is fused.tier
    assert agg.score == fused.score
    assert agg.reasons == fused.reasons
    assert agg.contributing_stages == fused.contributing_stages
    assert agg.notes == fused.notes
    assert agg.stage_results == tuple(results)


def test_aggregate_no_results_is_inconclusive():
    agg = aggregate([])
    assert agg.tier is ConfidenceTier.INCONCLUSIVE
    assert agg.score is None
    assert agg.findings == ()


def test_aggregate_failed_stage_noted_via_fusion():
    results = [
        _stage_result(ARITH, ConfidenceTier.MEDIUM, 65, findings=[_finding(ARITH, ConfidenceTier.MEDIUM)]),
        _stage_result(FONT, ConfidenceTier.INCONCLUSIVE, ok=False, error="boom"),
    ]
    agg = aggregate(results)
    assert any("font_forensics" in n and "boom" in n for n in agg.notes)


# --------------------------------------------------------------------------- #
# Finding flattening: stable ids, tier carried, score is None (no per-finding
# numeric score exists on the core Finding), bbox is None this slice.
# --------------------------------------------------------------------------- #

def test_flatten_assigns_stable_ids_and_carries_tier():
    findings = [_finding(REV, ConfidenceTier.HIGH), _finding(REV, ConfidenceTier.MEDIUM)]
    results = [_stage_result(REV, ConfidenceTier.HIGH, 90, findings=findings)]
    agg = aggregate(results)
    assert [f.finding_id for f in agg.findings] == ["revision_recovery-0", "revision_recovery-1"]
    assert agg.findings[0].tier is ConfidenceTier.HIGH
    assert agg.findings[1].tier is ConfidenceTier.MEDIUM
    assert all(f.score is None for f in agg.findings)
    # No payload/location on these synthetic findings -> bbox stays None.
    assert all(f.bbox is None for f in agg.findings)


def test_flatten_token_class_amount_passthrough():
    f = _finding(REV, high_value="amount", before="5,000", after="50,000")
    agg = aggregate([_stage_result(REV, ConfidenceTier.HIGH, 90, findings=[f])])
    assert agg.findings[0].token_class == "amount"


def test_flatten_token_class_prose_when_text_but_no_high_value():
    f = _finding(REV, before="paid in full", after="not paid")
    agg = aggregate([_stage_result(REV, ConfidenceTier.MEDIUM, 50, findings=[f])])
    assert agg.findings[0].token_class == "prose"


def test_flatten_token_class_none_when_no_text():
    f = _finding(PROV, before=None, after=None, high_value=None)
    agg = aggregate([_stage_result(PROV, ConfidenceTier.LOW, 10, findings=[f])])
    assert agg.findings[0].token_class is None


# --------------------------------------------------------------------------- #
# _finding_bbox: revision_recovery location -> normalized [0,1] union BBox,
# with a positional-integrity guard (page + object_ids must match the rich
# finding before the positional index is trusted).
# --------------------------------------------------------------------------- #

def _rr_rich(page=2, object_ids=("4 0",), location=None) -> RRFinding:
    return RRFinding(
        from_revision=0, to_revision=1, page_index=page, object_ids=object_ids,
        object_classes=(ObjectChangeClass.CONTENT,), token_changes=(),
        is_high_value=False, high_value_kind=None, summary="x", location=location,
    )


def test_finding_bbox_normalizes_location_union():
    loc = FindingLocation(
        boxes=(
            BoxPt(x0=120, top=100, x1=170, bottom=116),
            BoxPt(x0=72, top=98, x1=110, bottom=116),
        ),
        page_width_pt=612.0, page_height_pt=792.0, page_rotation=0,
    )
    payload = SimpleNamespace(findings=[_rr_rich(location=loc)])
    core = _finding(REV, page=2, object_ids=("4 0",))
    agg = aggregate([_stage_result(REV, ConfidenceTier.HIGH, 90, findings=[core], payload=payload)])
    bb = agg.findings[0].bbox
    assert bb is not None
    assert bb.x0 == pytest.approx(72 / 612)
    assert bb.y0 == pytest.approx(98 / 792)
    assert bb.x1 == pytest.approx(170 / 612)
    assert bb.y1 == pytest.approx(116 / 792)


def test_finding_bbox_none_without_location():
    payload = SimpleNamespace(findings=[_rr_rich(location=None)])
    core = _finding(REV, page=2, object_ids=("4 0",))
    agg = aggregate([_stage_result(REV, ConfidenceTier.HIGH, 90, findings=[core], payload=payload)])
    assert agg.findings[0].bbox is None


def test_finding_bbox_guard_rejects_page_mismatch():
    loc = FindingLocation(
        boxes=(BoxPt(x0=10, top=10, x1=20, bottom=20),),
        page_width_pt=612.0, page_height_pt=792.0,
    )
    payload = SimpleNamespace(findings=[_rr_rich(page=2, location=loc)])
    core = _finding(REV, page=5, object_ids=("4 0",))  # page disagrees with rich
    agg = aggregate([_stage_result(REV, ConfidenceTier.HIGH, 90, findings=[core], payload=payload)])
    assert agg.findings[0].bbox is None


def test_finding_bbox_guard_rejects_object_id_mismatch():
    loc = FindingLocation(
        boxes=(BoxPt(x0=10, top=10, x1=20, bottom=20),),
        page_width_pt=612.0, page_height_pt=792.0,
    )
    payload = SimpleNamespace(findings=[_rr_rich(object_ids=("4 0",), location=loc)])
    core = _finding(REV, page=2, object_ids=("9 0",))  # object id disagrees
    agg = aggregate([_stage_result(REV, ConfidenceTier.HIGH, 90, findings=[core], payload=payload)])
    assert agg.findings[0].bbox is None


def test_finding_bbox_none_for_non_revision_recovery_stage():
    agg = aggregate([_stage_result(PROV, ConfidenceTier.LOW, 10, findings=[_finding(PROV)])])
    assert agg.findings[0].bbox is None


def test_finding_bbox_end_to_end_amount_edit():
    import sys
    from pathlib import Path

    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import make_localization_fixtures as F

    from pdf_forgery.revision_recovery.adapter import report_to_stage_result
    from pdf_forgery.revision_recovery.analyze import analyze_bytes

    _, forged = F.amount_pair()
    stage_result = report_to_stage_result(analyze_bytes(forged, "amount.pdf"))
    agg = aggregate([stage_result])
    boxed = [f for f in agg.findings if f.bbox is not None]
    assert len(boxed) == 1
    bb = boxed[0].bbox
    assert 0 <= bb.x0 < bb.x1 <= 1
    assert 0 <= bb.y0 < bb.y1 <= 1


# --------------------------------------------------------------------------- #
# _finding_type per stage — derived from the rich payload, positionally
# correlated to stage_result.findings (see aggregate.py for the invariant).
# --------------------------------------------------------------------------- #

def test_finding_type_revision_recovery_from_object_classes():
    rich = SimpleNamespace(object_classes=(ObjectChangeClass.CONTENT,))
    payload = SimpleNamespace(findings=[rich])
    f = _finding(REV)
    agg = aggregate([_stage_result(REV, ConfidenceTier.HIGH, 90, findings=[f], payload=payload)])
    assert agg.findings[0].type == "content_edit"


def test_finding_type_revision_recovery_priority_content_over_overlay():
    # CONTENT must win even when OVERLAY is also present (most-severe-first).
    rich = SimpleNamespace(object_classes=(ObjectChangeClass.OVERLAY, ObjectChangeClass.CONTENT))
    payload = SimpleNamespace(findings=[rich])
    agg = aggregate([_stage_result(REV, ConfidenceTier.HIGH, 90, findings=[_finding(REV)], payload=payload)])
    assert agg.findings[0].type == "content_edit"


def test_finding_type_revision_recovery_falls_back_without_payload():
    f = _finding(REV, high_value="amount")
    agg = aggregate([_stage_result(REV, ConfidenceTier.HIGH, 90, findings=[f], payload=None)])
    assert agg.findings[0].type == "amount"


def test_finding_type_font_forensics_from_kind():
    rich = SimpleNamespace(kind=FontFindingKind.INTRA_TOKEN_FONT_MIX)
    payload = SimpleNamespace(findings=[rich])
    agg = aggregate([_stage_result(FONT, ConfidenceTier.HIGH, 80, findings=[_finding(FONT)], payload=payload)])
    assert agg.findings[0].type == "intra_token_font_mix"


def test_finding_type_invoice_arithmetic_from_relationship_kind():
    rich = SimpleNamespace(relationship_kind=RelationshipKind.GRAND_TOTAL)
    payload = SimpleNamespace(findings=[rich])
    agg = aggregate([_stage_result(ARITH, ConfidenceTier.MEDIUM, 65, findings=[_finding(ARITH)], payload=payload)])
    assert agg.findings[0].type == "grand_total"


def test_finding_type_provenance_metadata_from_kind():
    rich = SimpleNamespace(kind=ProvenanceFindingKind.MODDATE_AFTER_CREATION)
    payload = SimpleNamespace(findings=[rich])
    agg = aggregate([_stage_result(PROV, ConfidenceTier.MEDIUM, 55, findings=[_finding(PROV)], payload=payload)])
    assert agg.findings[0].type == "moddate_after_creation"


def test_finding_type_ocr_crosscheck_skips_agree_like_the_adapter():
    # AGREE divergences never become core Findings; the surviving findings list
    # has 2 entries that must line up with the 2nd/3rd raw divergences.
    divergences = [
        SimpleNamespace(type=DivergenceType.AGREE),
        SimpleNamespace(type=DivergenceType.MISMATCH),
        SimpleNamespace(type=DivergenceType.EMBEDDED_ONLY),
    ]
    payload = SimpleNamespace(result=SimpleNamespace(divergences=divergences))
    findings = [_finding(OCR), _finding(OCR)]
    agg = aggregate([_stage_result(OCR, ConfidenceTier.HIGH, 90, findings=findings, payload=payload)])
    assert [f.type for f in agg.findings] == ["mismatch", "embedded_only"]


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("ela", "image_ela"),
        ("double_jpeg", "image_double_jpeg"),
        ("jpeg_grid", "image_jpeg_grid"),
        ("noise_inconsistency", "image_noise"),
        ("copy_move", "image_copy_move"),
        ("unknown_method", "image_anomaly"),
    ],
)
def test_finding_type_image_forensics_single_method(method, expected):
    region = TamperRegion(
        page_index=0,
        page_bbox=None,
        methods=(method,),
        strength=0.8,
        co_located=False,
        high_value=False,
    )
    report = ImageForensicsReport(
        tier=ConfidenceTier.MEDIUM,
        score=60,
        findings=(
            RegionFinding(
                region=region,
                tier=ConfidenceTier.MEDIUM,
                reason="localized test signal",
            ),
        ),
    )
    agg = aggregate([report_to_stage_result(report)])
    assert agg.findings[0].type == expected


def test_finding_type_image_forensics_colocated_is_splice():
    region = TamperRegion(
        page_index=0,
        page_bbox=None,
        methods=("ela", "noise_inconsistency"),
        strength=0.9,
        co_located=True,
        high_value=True,
    )
    report = ImageForensicsReport(
        tier=ConfidenceTier.HIGH,
        score=95,
        findings=(
            RegionFinding(
                region=region,
                tier=ConfidenceTier.HIGH,
                reason="two co-located signals",
            ),
        ),
    )
    agg = aggregate([report_to_stage_result(report)])
    assert agg.findings[0].type == "image_splice"


def test_finding_type_image_forensics_fallbacks_are_image_specific():
    no_payload = aggregate(
        [
            _stage_result(
                IMG,
                ConfidenceTier.MEDIUM,
                60,
                findings=[_finding(IMG, ConfidenceTier.MEDIUM)],
            )
        ]
    )
    assert no_payload.findings[0].type == "image_anomaly"

    region = SimpleNamespace(
        page_index=1, co_located=False, methods=("ela",)
    )
    mismatched_payload = SimpleNamespace(
        findings=[SimpleNamespace(region=region)]
    )
    mismatch = aggregate(
        [
            _stage_result(
                IMG,
                ConfidenceTier.MEDIUM,
                60,
                findings=[_finding(IMG, ConfidenceTier.MEDIUM, page=0)],
                payload=mismatched_payload,
            )
        ]
    )
    assert mismatch.findings[0].type == "image_anomaly"


# --------------------------------------------------------------------------- #
# PHI-scrub boundary
# --------------------------------------------------------------------------- #

def test_to_advisory_input_projects_clean_descriptors():
    f = _finding(REV, high_value="amount", before="5,000", after="50,000", reason="amount changed")
    results = [_stage_result(REV, ConfidenceTier.HIGH, 90, findings=[f])]
    agg = aggregate(results)
    ai = to_advisory_input(agg)
    assert ai.tier is ConfidenceTier.HIGH
    assert len(ai.findings) == 1
    assert ai.findings[0].finding_id == "revision_recovery-0"
    # Nothing about the raw before/after text or the reason string crosses.
    for field in dataclasses.fields(ai.findings[0]):
        assert field.name not in ("before", "after", "reason", "summary", "payload")


def test_assert_advisory_safe_passes_on_clean_input():
    ai = AdvisoryInput(
        tier=ConfidenceTier.LOW, score=10,
        stages=(AdvisoryStage(stage=REV, tier=ConfidenceTier.LOW, score=10, ok=True),),
        findings=(),
        notes=(),
    )
    assert_advisory_safe(ai)  # must not raise


def test_assert_advisory_safe_blocks_planted_raw_text_field():
    finding = AdvisoryFinding(
        finding_id="revision_recovery-0", stage=REV, type="content_edit",
        tier=ConfidenceTier.HIGH, score=None, token_class="amount", page=0, bbox=None,
    )
    # Plant a raw-text field that is NOT part of the AdvisoryFinding contract —
    # frozen dataclasses have no __slots__, so this is possible at runtime and
    # is exactly the leak assert_advisory_safe must catch.
    object.__setattr__(finding, "raw_text", "the amount was changed from 5,000 to 50,000")
    ai = AdvisoryInput(
        tier=ConfidenceTier.HIGH, score=90,
        stages=(),
        findings=(finding,),
        notes=(),
    )
    with pytest.raises(ValueError):
        assert_advisory_safe(ai)


def test_assert_advisory_safe_blocks_freetext_leaking_into_a_token_field():
    finding = AdvisoryFinding(
        finding_id="revision_recovery-0", stage=REV,
        type="the policy number was changed from ABC123 to XYZ789",  # leaked content
        tier=ConfidenceTier.HIGH, score=None, token_class="id", page=0, bbox=None,
    )
    ai = AdvisoryInput(tier=ConfidenceTier.HIGH, score=90, stages=(), findings=(finding,), notes=())
    with pytest.raises(ValueError):
        assert_advisory_safe(ai)


def test_bbox_geometry_is_not_phi_and_survives_the_boundary():
    bbox = BBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4)
    finding = AdvisoryFinding(
        finding_id="ocr_crosscheck-0", stage=OCR, type="mismatch",
        tier=ConfidenceTier.HIGH, score=None, token_class="amount", page=2, bbox=bbox,
    )
    ai = AdvisoryInput(tier=ConfidenceTier.HIGH, score=90, stages=(), findings=(finding,), notes=())
    assert_advisory_safe(ai)  # geometry alone is not a leak
    assert ai.findings[0].bbox == bbox


# --------------------------------------------------------------------------- #
# Advisory: grounded, id-citing, INCONCLUSIVE honesty, graceful degradation.
# --------------------------------------------------------------------------- #

def _advisory_input(tier, score, findings=()):
    stages = (AdvisoryStage(stage=REV, tier=tier, score=score, ok=True),)
    return AdvisoryInput(tier=tier, score=score, stages=stages, findings=tuple(findings), notes=())


def test_stub_engine_cites_only_supplied_ids():
    finding = AdvisoryFinding(
        finding_id="revision_recovery-0", stage=REV, type="content_edit",
        tier=ConfidenceTier.HIGH, score=None, token_class="amount", page=0, bbox=None,
    )
    ai = _advisory_input(ConfidenceTier.HIGH, 90, [finding])
    out = generate_advisory(ai, engine=StubAdvisoryEngine())
    assert out.model == "stub"
    # Stub now produces group_explanations, not per-finding rationales.
    all_cited = {fid for g in out.group_explanations for fid in g.finding_ids}
    assert all_cited == {"revision_recovery-0"}
    assert out.group_explanations[0].what_we_found  # non-empty explanation
    assert out.group_explanations[0].why_it_matters
    assert out.group_explanations[0].what_to_check


def test_advisory_inconclusive_is_rendered_honestly_not_as_clean():
    ai = _advisory_input(ConfidenceTier.INCONCLUSIVE, None, [])
    out = generate_advisory(ai, engine=StubAdvisoryEngine())
    statement = out.tier_statement.lower()
    assert "could not assess" in statement
    # Must explicitly disclaim "clean", never assert it outright.
    assert "not the same as clean" in statement or "clean" not in statement


def test_advisory_disabled_returns_templated_output_without_calling_engine():
    class Boom:
        name = "should-not-be-called"

        def is_available(self):
            raise AssertionError("engine must not be touched when advisory_enabled=False")

        def generate(self, messages):
            raise AssertionError("engine must not be touched when advisory_enabled=False")

    ai = _advisory_input(ConfidenceTier.LOW, 10, [])
    out = generate_advisory(ai, engine=Boom(), config=AggregateConfig(advisory_enabled=False))
    assert out.model == "disabled"


def test_advisory_degrades_when_engine_unavailable():
    class Unavailable:
        name = "local_llm"

        def is_available(self):
            return False

        def generate(self, messages):
            raise AssertionError("must not be called when unavailable")

    ai = _advisory_input(ConfidenceTier.MEDIUM, 50, [])
    out = generate_advisory(ai, engine=Unavailable())
    assert "unavailable" in out.model
    assert out.summary  # still produced a usable, templated explanation


def test_advisory_degrades_when_engine_raises():
    class Explodes:
        name = "flaky"

        def is_available(self):
            return True

        def generate(self, messages):
            raise RuntimeError("model server is down")

    ai = _advisory_input(ConfidenceTier.HIGH, 90, [])
    out = generate_advisory(ai, engine=Explodes())
    assert "error" in out.model


def test_advisory_degrades_when_engine_cites_unknown_id():
    from pdf_forgery.aggregate.models import AdvisoryOutput, GroupExplanation

    class CitesGarbage:
        name = "hallucinating"

        def is_available(self):
            return True

        def generate(self, messages):
            return AdvisoryOutput(
                summary="x", tier_statement="y",
                group_explanations=(
                    GroupExplanation(
                        finding_ids=("not-a-real-id",),
                        label="x", what_we_found="y", why_it_matters="z", what_to_check="w",
                    ),
                ),
                model=self.name,
            )

    finding = AdvisoryFinding(
        finding_id="revision_recovery-0", stage=REV, type="content_edit",
        tier=ConfidenceTier.HIGH, score=None, token_class="amount", page=0, bbox=None,
    )
    ai = _advisory_input(ConfidenceTier.HIGH, 90, [finding])
    out = generate_advisory(ai, engine=CitesGarbage())
    assert "malformed" in out.model
    # Fallback produces groups citing only valid ids.
    all_cited = {fid for g in out.group_explanations for fid in g.finding_ids}
    assert all_cited.issubset({"revision_recovery-0"})


# --------------------------------------------------------------------------- #
# Grouping unit tests
# --------------------------------------------------------------------------- #

def test_group_findings_collapses_identical_type():
    """N near-identical ocr_crosscheck findings → 1 group, count=N, correct pages."""
    from pdf_forgery.aggregate.advisory import _group_findings

    findings = [
        AdvisoryFinding(
            finding_id=f"ocr_crosscheck-{i}", stage=OCR, type="embedded_only",
            tier=ConfidenceTier.MEDIUM, score=None, token_class="id", page=i, bbox=None,
        )
        for i in range(5)
    ]
    ai = AdvisoryInput(
        tier=ConfidenceTier.MEDIUM, score=50,
        stages=(AdvisoryStage(stage=OCR, tier=ConfidenceTier.MEDIUM, score=50, ok=True),),
        findings=tuple(findings),
    )
    groups = _group_findings(ai)
    assert len(groups) == 1
    g = groups[0]
    assert g.count == 5
    assert g.pages == (0, 1, 2, 3, 4)
    assert set(g.finding_ids) == {f"ocr_crosscheck-{i}" for i in range(5)}
    assert g.tier is ConfidenceTier.MEDIUM


def test_group_findings_escalates_to_max_tier():
    """Max tier is picked when findings in same group have different tiers."""
    from pdf_forgery.aggregate.advisory import _group_findings

    findings = [
        AdvisoryFinding(
            finding_id="ocr_crosscheck-0", stage=OCR, type="mismatch",
            tier=ConfidenceTier.LOW, score=None, token_class=None, page=0, bbox=None,
        ),
        AdvisoryFinding(
            finding_id="ocr_crosscheck-1", stage=OCR, type="mismatch",
            tier=ConfidenceTier.HIGH, score=None, token_class=None, page=1, bbox=None,
        ),
    ]
    ai = AdvisoryInput(
        tier=ConfidenceTier.HIGH, score=80,
        stages=(), findings=tuple(findings),
    )
    groups = _group_findings(ai)
    assert len(groups) == 1
    assert groups[0].tier is ConfidenceTier.HIGH


def test_group_findings_splits_different_types():
    """Different (type, token_class) combos stay in separate groups."""
    from pdf_forgery.aggregate.advisory import _group_findings

    findings = [
        AdvisoryFinding(
            finding_id="ocr_crosscheck-0", stage=OCR, type="embedded_only",
            tier=ConfidenceTier.MEDIUM, score=None, token_class="id", page=0, bbox=None,
        ),
        AdvisoryFinding(
            finding_id="ocr_crosscheck-1", stage=OCR, type="ocr_only",
            tier=ConfidenceTier.MEDIUM, score=None, token_class="id", page=0, bbox=None,
        ),
    ]
    ai = AdvisoryInput(
        tier=ConfidenceTier.MEDIUM, score=50, stages=(), findings=tuple(findings),
    )
    groups = _group_findings(ai)
    assert len(groups) == 2


def test_stub_advisory_uses_glossary_language():
    """Stub advisory explanation should mention glossary meaning, not just type name."""
    finding = AdvisoryFinding(
        finding_id="ocr_crosscheck-0", stage=OCR, type="embedded_only",
        tier=ConfidenceTier.HIGH, score=None, token_class="id", page=2, bbox=None,
    )
    ai = _advisory_input(ConfidenceTier.HIGH, 80, [finding])
    out = generate_advisory(ai, engine=StubAdvisoryEngine())
    expl = out.group_explanations[0]
    # The glossary entry for embedded_only mentions "text layer" — not just the type name.
    assert "text layer" in expl.what_we_found.lower() or "text layer" in expl.label.lower() or "text" in expl.what_we_found.lower()
    assert expl.what_to_check  # non-empty reviewer action


def test_image_forensics_advisory_is_detector_specific():
    finding = AdvisoryFinding(
        finding_id="image_forensics-0",
        stage=IMG,
        type="image_ela",
        tier=ConfidenceTier.MEDIUM,
        score=None,
        token_class=None,
        page=0,
        bbox=None,
    )
    out = generate_advisory(
        _advisory_input(ConfidenceTier.MEDIUM, 60, [finding]),
        engine=StubAdvisoryEngine(),
    )
    explanation = out.group_explanations[0]
    assert "error-level analysis" in explanation.what_we_found.lower()
    assert "flagged one region" in explanation.what_we_found.lower()
    assert "differently compressed area" in explanation.what_to_check.lower()
    assert explanation.what_to_check != (
        "Review this finding in the context of the other detector results."
    )


# --------------------------------------------------------------------------- #
# Glossary coverage — every emitted type token must have a real entry
# --------------------------------------------------------------------------- #

def test_glossary_covers_all_emitted_type_tokens():
    """Every type token _finding_type() can produce must have a non-generic glossary entry."""
    from pdf_forgery.aggregate.aggregate import _IMAGE_METHOD_TYPE, _OBJECT_CLASS_TYPE
    from pdf_forgery.aggregate.glossary import _GENERIC, get_glossary_entry

    all_tokens: set[str] = set()

    # ocr_crosscheck: DivergenceType minus agree
    for dt in DivergenceType:
        if dt.value != "agree":
            all_tokens.add(dt.value)

    # revision_recovery: _OBJECT_CLASS_TYPE tokens
    for _, type_token in _OBJECT_CLASS_TYPE:
        all_tokens.add(type_token)

    # font_forensics: FontFindingKind (enum aliases share a value; deduped by set)
    for fk in FontFindingKind:
        all_tokens.add(fk.value)

    # invoice_arithmetic: RelationshipKind
    for rk in RelationshipKind:
        all_tokens.add(rk.value)

    # provenance_metadata: ProvenanceFindingKind
    for pk in ProvenanceFindingKind:
        all_tokens.add(pk.value)

    # image_forensics: co-located, per-method, and image-specific fallback.
    all_tokens.update(_IMAGE_METHOD_TYPE.values())
    all_tokens.update(("image_splice", "image_anomaly"))

    missing = sorted(t for t in all_tokens if get_glossary_entry(t) is _GENERIC)
    assert missing == [], f"These tokens fall to the generic glossary entry: {missing}"


# --------------------------------------------------------------------------- #
# Markdown summary structure
# --------------------------------------------------------------------------- #

def test_stub_advisory_summary_is_markdown():
    """Stub summary for a document with findings should use Markdown bullets and bold."""
    findings = [
        AdvisoryFinding(
            finding_id="ocr_crosscheck-0", stage=OCR, type="embedded_only",
            tier=ConfidenceTier.HIGH, score=None, token_class="id", page=0, bbox=None,
        ),
        AdvisoryFinding(
            finding_id="invoice_arithmetic-0", stage="invoice_arithmetic", type="line_item",
            tier=ConfidenceTier.MEDIUM, score=None, token_class="amount", page=0, bbox=None,
        ),
    ]
    ai = AdvisoryInput(
        tier=ConfidenceTier.HIGH, score=80,
        stages=(
            AdvisoryStage(stage=OCR, tier=ConfidenceTier.HIGH, score=80, ok=True),
            AdvisoryStage(stage="invoice_arithmetic", tier=ConfidenceTier.MEDIUM, score=60, ok=True),
        ),
        findings=tuple(findings),
    )
    out = generate_advisory(ai, engine=StubAdvisoryEngine())
    summary = out.summary
    # Must contain at least one Markdown bullet
    assert "- **" in summary, "summary should contain at least one Markdown bullet"
    # Must bold the overall confidence tier
    assert "**HIGH" in summary, "summary should bold the tier"
    # Must not be a single run-on sentence (should have newlines)
    assert "\n" in summary, "summary should have newlines separating sections"
    # tier_statement remains plain text (no Markdown bold)
    assert "**" not in out.tier_statement, "tier_statement should stay plain text"
