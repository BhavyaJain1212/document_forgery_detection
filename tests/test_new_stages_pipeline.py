"""Stage-protocol conformance, adapter round-trip, and pipeline integration
for the two new single-document stages.
"""

from __future__ import annotations

from pdf_forgery.core import ConfidenceTier, Stage
from pdf_forgery.core.context import AnalysisContext
from pdf_forgery.invoice_arithmetic import (
    InvoiceArithmeticStage,
    InvoiceReport,
    render_stage_json,
    render_stage_summary,
    stage_result_to_report,
)
from pdf_forgery.provenance_metadata import (
    ProvenanceMetadataStage,
    ProvenanceReport,
)
from pdf_forgery.provenance_metadata import (
    stage_result_to_report as prov_to_report,
)
from pdf_forgery.pipeline import run_pipeline_on_path


def test_stages_conform_to_protocol():
    assert isinstance(InvoiceArithmeticStage(), Stage)
    assert isinstance(ProvenanceMetadataStage(), Stage)


def test_invoice_stage_run_and_adapter_roundtrip(invoice_tamper_pdf):
    raw = invoice_tamper_pdf.read_bytes()
    stage = InvoiceArithmeticStage()
    with AnalysisContext(raw, path=str(invoice_tamper_pdf)) as ctx:
        result = stage.run(raw, ctx)
    assert result.stage == "invoice_arithmetic"
    assert result.tier is ConfidenceTier.HIGH
    assert result.findings  # at least one core Finding
    # payload round-trips back to the rich report.
    report = stage_result_to_report(result)
    assert isinstance(report, InvoiceReport)
    # renderers operate on the StageResult via the preserved payload.
    assert "invoice_arithmetic" in render_stage_json(result)
    assert "ADVISORY" in render_stage_summary(result)


def test_provenance_stage_run_and_payload(sejda_tampered_pdf):
    raw = sejda_tampered_pdf.read_bytes()
    stage = ProvenanceMetadataStage()
    with AnalysisContext(raw, path=str(sejda_tampered_pdf)) as ctx:
        result = stage.run(raw, ctx)
    assert result.stage == "provenance_metadata"
    assert result.tier is ConfidenceTier.MEDIUM
    assert isinstance(prov_to_report(result), ProvenanceReport)


def test_pipeline_runs_both_new_stages_together(invoice_tamper_pdf):
    stages = [InvoiceArithmeticStage(), ProvenanceMetadataStage()]
    results = run_pipeline_on_path(invoice_tamper_pdf, stages)
    assert [r.stage for r in results] == ["invoice_arithmetic", "provenance_metadata"]
    assert all(r.ok for r in results)
    # invoice arithmetic catches the convergence tamper.
    assert results[0].tier is ConfidenceTier.HIGH
    # provenance never independently HIGH.
    assert results[1].tier is not ConfidenceTier.HIGH


def test_pipeline_graceful_on_bad_path():
    stages = [InvoiceArithmeticStage(), ProvenanceMetadataStage()]
    results = run_pipeline_on_path("/no/such/file.pdf", stages)
    assert len(results) == 2
    assert all(r.ok is False for r in results)
