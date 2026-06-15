"""Regression coverage for page- and logical-invoice-aware arithmetic."""

from __future__ import annotations

from pdf_forgery.core import ConfidenceTier
from pdf_forgery.invoice_arithmetic import (
    analyze_path,
    report_to_dict,
    report_to_stage_result,
)
from pdf_forgery.invoice_arithmetic.models import (
    RelationshipKind,
    SegmentationConfidence,
)


def test_two_clean_invoices_are_scoped_independently(invoice_two_clean_pdf):
    report = analyze_path(invoice_two_clean_pdf)

    assert report.tier is ConfidenceTier.LOW
    assert report.findings == ()
    assert [invoice.logical_invoice_id for invoice in report.logical_invoices] == [
        "invoice-001", "invoice-002"
    ]
    assert [invoice.page_numbers for invoice in report.logical_invoices] == [(1,), (2,)]

    subtotals = [
        relationship for relationship in report.relationships
        if relationship.kind is RelationshipKind.SUBTOTAL_SUM
    ]
    assert [(rel.logical_invoice_id, rel.expected, rel.stated) for rel in subtotals] == [
        ("invoice-001", 350.0, 350.0),
        ("invoice-002", 549.69, 549.69),
    ]


def test_repeated_header_continues_one_invoice(invoice_repeated_header_pdf):
    report = analyze_path(invoice_repeated_header_pdf)

    assert report.tier is ConfidenceTier.LOW
    assert len(report.logical_invoices) == 1
    invoice = report.logical_invoices[0]
    assert invoice.page_numbers == (1, 2)
    assert invoice.segmentation_confidence is SegmentationConfidence.HIGH
    subtotal = next(
        rel for rel in report.relationships if rel.kind is RelationshipKind.SUBTOTAL_SUM
    )
    assert subtotal.expected == 899.69
    assert subtotal.stated == 899.69


def test_confident_headerless_continuation_is_included(invoice_headerless_pdf):
    report = analyze_path(invoice_headerless_pdf)

    assert report.tier is ConfidenceTier.LOW
    assert len(report.logical_invoices) == 1
    invoice = report.logical_invoices[0]
    assert invoice.page_numbers == (1, 2)
    assert invoice.segmentation_confidence is SegmentationConfidence.MEDIUM
    assert any(table.source == "inferred" for table in invoice.tables)
    subtotal = next(
        rel for rel in report.relationships if rel.kind is RelationshipKind.SUBTOTAL_SUM
    )
    assert subtotal.expected == 899.69
    assert subtotal.within_tolerance is True


def test_ambiguous_boundary_keeps_rows_and_suppresses_subtotal(invoice_ambiguous_pdf):
    report = analyze_path(invoice_ambiguous_pdf)

    assert report.tier is ConfidenceTier.LOW
    row_checks = [
        rel for rel in report.relationships if rel.kind is RelationshipKind.LINE_ITEM
    ]
    assert len(row_checks) == 4
    assert all(rel.within_tolerance for rel in row_checks)
    assert not any(
        rel.kind is RelationshipKind.SUBTOTAL_SUM for rel in report.relationships
    )
    suppressed = [
        check for check in report.suppressed_checks
        if check.kind is RelationshipKind.SUBTOTAL_SUM
    ]
    assert len(suppressed) == 1
    assert suppressed[0].segmentation_confidence is SegmentationConfidence.AMBIGUOUS
    assert suppressed[0].logical_invoice_id == "invoice-002"


def test_adapter_exposes_location_and_segmentation(invoice_ambiguous_pdf):
    payload = report_to_dict(analyze_path(invoice_ambiguous_pdf))

    relationship = payload["relationships"][0]
    for key in (
        "logical_invoice_id", "segmentation_confidence", "segmentation_basis",
        "page", "page_number", "bbox", "role_label", "stated", "expected",
        "equation", "equation_kind",
    ):
        assert key in relationship

    suppressed = payload["suppressed_checks"][0]
    assert suppressed["kind"] == RelationshipKind.SUBTOTAL_SUM.value
    assert suppressed["page_numbers"] == [2]
    assert suppressed["segmentation_confidence"] == "ambiguous"


def test_core_adapter_carries_finding_metadata(invoice_tamper_pdf):
    stage = report_to_stage_result(analyze_path(invoice_tamper_pdf))
    finding = next(item for item in stage.findings if item.before == "30000.0")
    labels = {evidence.label for evidence in finding.evidence}
    assert {
        "logical_invoice_id", "segmentation_confidence", "segmentation_basis",
        "page_number", "bbox", "role_label", "expected", "stated",
        "equation", "equation_kind",
    }.issubset(labels)
