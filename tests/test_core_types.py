"""Tests for the stage-agnostic core schema and AnalysisContext.

Covers the shared vocabulary every stage speaks (ConfidenceTier / Evidence /
Finding / StageResult), the Stage protocol, and the lazily-cached
AnalysisContext.
"""

from __future__ import annotations

from pdf_forgery.core import (
    AnalysisContext,
    ConfidenceTier,
    Evidence,
    Finding,
    Stage,
    StageResult,
)
from pdf_forgery.revision_recovery import RevisionRecoveryStage
from pdf_forgery.revision_recovery.models import ConfidenceTier as RRConfidenceTier


# --------------------------------------------------------------------------- #
# ConfidenceTier is the ONE shared enum
# --------------------------------------------------------------------------- #

def test_confidence_tier_is_shared_with_revision_recovery():
    # revision_recovery now re-exports the core enum: same object, same members.
    assert RRConfidenceTier is ConfidenceTier
    assert RRConfidenceTier.HIGH is ConfidenceTier.HIGH


def test_confidence_tier_values():
    assert [t.value for t in ConfidenceTier] == [
        "inconclusive",
        "low",
        "medium",
        "high",
    ]
    # ``str`` mixin -> serializes to its plain value.
    assert ConfidenceTier.HIGH == "high"


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #

def test_finding_and_evidence_defaults():
    f = Finding(stage="s", tier=ConfidenceTier.LOW, reason="r")
    assert f.page is None
    assert f.object_ids == ()
    assert f.before is None and f.after is None
    assert f.high_value is None
    assert f.evidence == ()

    e = Evidence(label="amount", before="5,000", after="50,000")
    assert (e.before, e.after) == ("5,000", "50,000")


def test_stage_result_defaults_and_payload_repr_hidden():
    r = StageResult(stage="s", tier=ConfidenceTier.INCONCLUSIVE, score=None)
    assert r.findings == ()
    assert r.ok is True and r.error is None
    assert r.payload is None
    # payload is excluded from repr (it can be a large nested object).
    r2 = StageResult(
        stage="s", tier=ConfidenceTier.HIGH, score=95, payload={"big": "obj"}
    )
    assert "big" not in repr(r2)


# --------------------------------------------------------------------------- #
# Stage protocol
# --------------------------------------------------------------------------- #

def test_revision_recovery_stage_satisfies_protocol():
    stage = RevisionRecoveryStage()
    assert isinstance(stage, Stage)
    assert stage.name == "revision_recovery"


# --------------------------------------------------------------------------- #
# AnalysisContext: lazy + cached shared artifacts
# --------------------------------------------------------------------------- #

def test_context_caches_pikepdf_and_layouts(forged_pdf):
    raw = forged_pdf.read_bytes()
    with AnalysisContext(raw, path=str(forged_pdf)) as ctx:
        assert ctx.pdf_bytes == raw
        assert ctx.path == str(forged_pdf)

        doc1 = ctx.pikepdf_doc
        doc2 = ctx.pikepdf_doc
        assert doc1 is not None
        assert doc1 is doc2  # cached, not re-opened

        layouts1 = ctx.page_layouts
        layouts2 = ctx.page_layouts
        assert layouts1 is layouts2  # same cached list object
        assert len(layouts1) >= 1


def test_context_tolerates_garbage_bytes():
    ctx = AnalysisContext(b"not a pdf at all")
    assert ctx.pikepdf_doc is None  # reported as None, never raises
    assert ctx.page_layouts == []
    assert ctx.rasterized_pages() == []  # no backend / unrenderable -> []
    ctx.close()
