"""Tests for orchestration (analyze) + report rendering.

Fixtures are built inline with pikepdf (matching test_reconstruct.py) so the
tests do not depend on the Task-7 fixture generator. ``_incremental_text_edit``
performs a genuine incremental update overriding a page content stream so we have
a known-positive (HIGH) case; the base PDF alone is the known-negative
(INCONCLUSIVE) case.
"""

from __future__ import annotations

import io
import json

import pikepdf
from pikepdf import Array, Dictionary, Name, Pdf

from pdf_forgery.revision_recovery import (
    AnalysisReport,
    ConfidenceTier,
    analyze_bytes,
    render_json,
    render_summary,
    report_to_dict,
)
from pdf_forgery.revision_recovery.detect import detect


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _text_pdf(text: str) -> bytes:
    pdf = Pdf.new()
    stream = pdf.make_stream(f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode())
    font = pdf.make_indirect(
        Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
    )
    page = pdf.make_indirect(
        Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, 612, 792]),
            Contents=stream,
            Resources=Dictionary(Font=Dictionary(F1=font)),
        )
    )
    pdf.Root.Pages.Kids.append(page)
    pdf.Root.Pages.Count = 1
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _incremental_text_edit(base: bytes, new_text: str) -> bytes:
    """Override the page content stream via a genuine incremental update."""
    prev_sx = detect(base).valid_boundaries[-1].startxref.pointer
    with pikepdf.open(io.BytesIO(base)) as p:
        old_size = int(p.trailer.Size)
        root_num, root_gen = p.Root.objgen
        onum, ogen = p.pages[0].Contents.objgen

    body = f"BT /F1 24 Tf 72 700 Td ({new_text}) Tj ET".encode()
    obj = (
        f"{onum} {ogen} obj\n<< /Length {len(body)} >>\nstream\n".encode()
        + body
        + b"\nendstream\nendobj\n"
    )
    obj_offset = len(base)
    xref_offset = obj_offset + len(obj)
    xref = (
        b"xref\n"
        + f"{onum} 1\n".encode()
        + f"{obj_offset:010d} {ogen:05d} n \n".encode()
        + b"trailer\n"
        + f"<< /Size {old_size} /Root {root_num} {root_gen} R /Prev {prev_sx} >>\n".encode()
        + b"startxref\n"
        + f"{xref_offset}\n".encode()
        + b"%%EOF\n"
    )
    return base + obj + xref


def _positive() -> bytes:
    return _incremental_text_edit(_text_pdf("Amount payable: 5,000"), "Amount payable: 50,000")


def _negative() -> bytes:
    return _text_pdf("Amount payable: 5,000")


# --------------------------------------------------------------------------- #
# analyze_bytes
# --------------------------------------------------------------------------- #

def test_positive_is_high_with_evidence():
    report = analyze_bytes(_positive(), "pos.pdf")
    assert report.ok is True
    assert report.scoring is not None
    assert report.scoring.tier is ConfidenceTier.HIGH
    assert report.revision_count == 2
    assert len(report.findings) >= 1

    f = report.findings[0]
    assert f.from_revision == 0 and f.to_revision == 1
    assert f.page_index == 0
    assert "content" in [c.value for c in f.object_classes]
    assert f.object_ids  # at least one changed object id
    assert "5,000" in f.before_text
    assert "50,000" in f.after_text
    assert f.is_high_value is True


def test_negative_is_inconclusive_no_findings():
    report = analyze_bytes(_negative(), "neg.pdf")
    assert report.ok is True
    assert report.scoring is not None
    assert report.scoring.tier is ConfidenceTier.INCONCLUSIVE
    assert report.scoring.score is None
    assert report.revision_count == 1
    assert report.findings == ()


def test_analyze_never_raises_on_garbage():
    report = analyze_bytes(b"not a pdf at all", "junk.pdf")
    assert isinstance(report, AnalysisReport)
    assert report.ok is True  # the run completed; verdict just isn't HIGH
    assert report.scoring is not None


# --------------------------------------------------------------------------- #
# JSON rendering
# --------------------------------------------------------------------------- #

def test_render_json_single_is_object():
    report = analyze_bytes(_positive(), "pos.pdf")
    obj = json.loads(render_json(report))
    assert isinstance(obj, dict)
    assert obj["path"] == "pos.pdf"
    assert obj["scoring"]["tier"] == "high"
    assert obj["scoring"]["score"] == 95
    assert obj["findings"][0]["before_text"] == "5,000"
    assert obj["findings"][0]["after_text"] == "50,000"
    assert obj["findings"][0]["page_number"] == 1
    assert "advisory" in obj


def test_render_json_batch_is_array():
    reports = [analyze_bytes(_positive(), "a.pdf"), analyze_bytes(_negative(), "b.pdf")]
    arr = json.loads(render_json(reports))
    assert isinstance(arr, list) and len(arr) == 2
    assert arr[0]["scoring"]["tier"] == "high"
    assert arr[1]["scoring"]["tier"] == "inconclusive"


def test_report_to_dict_is_json_safe():
    report = analyze_bytes(_positive(), "pos.pdf")
    d = report_to_dict(report)
    json.dumps(d)  # must not raise — no enums/dataclasses leak through


# --------------------------------------------------------------------------- #
# Human summary
# --------------------------------------------------------------------------- #

def test_summary_shows_before_after_and_advisory():
    report = analyze_bytes(_positive(), "pos.pdf")
    text = render_summary(report)
    assert "HIGH" in text
    assert "ADVISORY" in text
    assert "before: 5,000" in text
    assert "after:  50,000" in text
    assert "pos.pdf" in text


def test_summary_negative_notes_later_stages():
    report = analyze_bytes(_negative(), "neg.pdf")
    text = render_summary(report)
    assert "INCONCLUSIVE" in text
    assert "later stages" in text.lower()


def test_summary_failed_report():
    bad = AnalysisReport(
        path="missing.pdf",
        ok=False,
        error="file not found",
        raw_size=0,
        candidate_count=0,
        revision_count=0,
        reconstruction_failures=0,
        scoring=None,
        findings=(),
        text_changes=(),
        object_diffs=(),
        notes=("file not found",),
    )
    text = render_summary(bad)
    assert "ERROR: file not found" in text
