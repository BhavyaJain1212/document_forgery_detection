"""Relationship evaluation, tolerance, localization, and convergence.

These build :class:`Table` / :class:`Cell` structures directly (no PDF) so the
arithmetic logic is tested in isolation from glyph extraction.
"""

from __future__ import annotations

from dataclasses import replace

from pdf_forgery.invoice_arithmetic.localize import annotate_convergence
from pdf_forgery.invoice_arithmetic.models import (
    Cell,
    Column,
    ColumnRole,
    LogicalInvoice,
    RelationshipKind,
    Row,
    SegmentationConfidence,
    Table,
)
from pdf_forgery.invoice_arithmetic.relationships import (
    evaluate_logical_invoice,
    evaluate_relationships,
)


def _cell(role: ColumnRole, value, x: float = 0.0, y: float = 0.0) -> Cell:
    text = "" if value is None else (str(int(value)) if value == int(value) else f"{value:.2f}")
    return Cell(
        text=text, value=value, role=role, page_index=0,
        bbox=(x, y, x + 10, y + 10),
    )


def _line_row(qty, rate, amount, y: float) -> Row:
    return Row(
        page_index=0, y=y,
        cells=(
            _cell(ColumnRole.QTY, qty, 100, y),
            _cell(ColumnRole.RATE, rate, 200, y),
            _cell(ColumnRole.AMOUNT, amount, 300, y),
        ),
    )


def _table(rows: list[Row]) -> Table:
    cols = (
        Column(role=ColumnRole.QTY, label="Qty", center=100, x0=95, x1=105),
        Column(role=ColumnRole.RATE, label="Rate", center=200, x0=195, x1=205),
        Column(role=ColumnRole.AMOUNT, label="Amount", center=300, x0=295, x1=305),
    )
    return Table(page_index=0, columns=cols, rows=tuple(rows), header_y=500.0)


def _on_page(table: Table, page_index: int) -> Table:
    rows = tuple(
        replace(
            row,
            page_index=page_index,
            cells=tuple(replace(cell, page_index=page_index) for cell in row.cells),
        )
        for row in table.rows
    )
    return replace(table, page_index=page_index, rows=rows)


# --------------------------------------------------------------------------- #
# Line item qty * rate = amount + tolerance
# --------------------------------------------------------------------------- #

def test_line_item_reconciles():
    table = _table([_line_row(3, 83.23, 249.69, 480)])
    rels = evaluate_relationships([table])
    assert len(rels) == 1
    assert rels[0].kind is RelationshipKind.LINE_ITEM
    assert rels[0].within_tolerance is True


def test_line_item_rounding_within_tolerance_does_not_flag():
    # 3 * 83.2283... shown as 249.69; expected 249.6849, delta 0.0051 < abs_tol.
    table = _table([_line_row(3, 83.2283, 249.69, 480)])
    rels = evaluate_relationships([table])
    assert rels[0].within_tolerance is True
    assert rels[0].is_gross is False


def test_line_item_gross_mismatch_flags():
    table = _table([_line_row(3, 83.23, 24019.69, 480)])
    rels = evaluate_relationships([table])
    rel = rels[0]
    assert rel.within_tolerance is False
    assert rel.is_gross is True
    assert rel.expected == 249.69
    assert rel.stated == 24019.69


def test_line_item_localizes_to_amount_cell():
    table = _table([_line_row(3, 83.23, 24019.69, 480)])
    rel = evaluate_relationships([table])[0]
    assert rel.output_cell is not None
    assert rel.output_cell.role is ColumnRole.AMOUNT
    assert rel.output_cell.text == "24019.69"


# --------------------------------------------------------------------------- #
# Rate-display-rounding band (Azure-style: 2-dp rate over a multi-dp true rate)
# --------------------------------------------------------------------------- #

def test_line_item_rate_display_rounding_does_not_flag():
    # 2434.87 * shown 0.08 = 194.79 but stated 204.53 (true rate ~0.084).
    # Far outside abs/rel tolerance, but inside the rate's printed-precision band.
    table = _table([_line_row(2434.87, 0.08, 204.53, 480)])
    rel = evaluate_relationships([table])[0]
    assert rel.within_tolerance is True


def test_rate_rounding_band_can_be_disabled():
    from pdf_forgery.invoice_arithmetic.config import InvoiceConfig

    table = _table([_line_row(2434.87, 0.08, 204.53, 480)])
    rel = evaluate_relationships([table], config=InvoiceConfig(rate_precision_aware=False))[0]
    assert rel.within_tolerance is False
    assert rel.is_gross is True


def test_genuine_tamper_outside_rate_band_still_flags():
    # 100x edit with operands untouched — far beyond any rounding band.
    table = _table([_line_row(3, 83.23, 24019.69, 480)])
    rel = evaluate_relationships([table])[0]
    assert rel.within_tolerance is False
    assert rel.is_gross is True


def test_moderate_amount_edit_not_masked_by_rate_band():
    # rate 0.08 band tops out at 2434.87 * 0.085 = 206.96; stated 300 is a real
    # edit and must NOT be swallowed by the precision band.
    table = _table([_line_row(2434.87, 0.08, 300.00, 480)])
    rel = evaluate_relationships([table])[0]
    assert rel.within_tolerance is False


# --------------------------------------------------------------------------- #
# Summary block: subtotal, grand total, GST
# --------------------------------------------------------------------------- #

def test_subtotal_and_grand_total_reconcile():
    table = _table([
        _line_row(2, 100.0, 200.0, 480),
        _line_row(3, 50.0, 150.0, 456),
    ])
    summary = {
        ColumnRole.SUBTOTAL: _cell(ColumnRole.SUBTOTAL, 350.0, 300, 400),
        ColumnRole.DISCOUNT: _cell(ColumnRole.DISCOUNT, 50.0, 300, 380),
        ColumnRole.CGST: _cell(ColumnRole.CGST, 27.0, 300, 360),
        ColumnRole.SGST: _cell(ColumnRole.SGST, 27.0, 300, 340),
        ColumnRole.GRAND_TOTAL: _cell(ColumnRole.GRAND_TOTAL, 354.0, 300, 320),
    }
    rels = evaluate_relationships([table], summary)
    kinds = {r.kind for r in rels}
    assert RelationshipKind.SUBTOTAL_SUM in kinds
    assert RelationshipKind.GRAND_TOTAL in kinds
    assert all(r.within_tolerance for r in rels)


def test_grand_total_broken_flags():
    table = _table([_line_row(2, 100.0, 200.0, 480), _line_row(1, 150.0, 150.0, 456)])
    summary = {
        ColumnRole.SUBTOTAL: _cell(ColumnRole.SUBTOTAL, 350.0, 300, 400),
        ColumnRole.GRAND_TOTAL: _cell(ColumnRole.GRAND_TOTAL, 999.0, 300, 320),
    }
    rels = evaluate_relationships([table], summary)
    gt = [r for r in rels if r.kind is RelationshipKind.GRAND_TOTAL][0]
    assert gt.within_tolerance is False


def test_duplicate_generic_subtotals_are_suppressed_not_guessed():
    first = _table([_line_row(2, 100.0, 200.0, 480), _line_row(1, 50.0, 50.0, 456)])
    second = _on_page(
        _table([_line_row(3, 100.0, 300.0, 480), _line_row(1, 25.0, 25.0, 456)]),
        1,
    )
    subtotals = (
        replace(_cell(ColumnRole.SUBTOTAL, 250.0, 300, 400), role_label="Subtotal"),
        replace(
            _cell(ColumnRole.SUBTOTAL, 575.0, 300, 400),
            page_index=1,
            role_label="Subtotal",
        ),
        replace(
            _cell(ColumnRole.GRAND_TOTAL, 575.0, 300, 360),
            page_index=1,
            role_label="Grand Total",
        ),
    )
    invoice = LogicalInvoice(
        logical_invoice_id="invoice-001",
        page_indexes=(0, 1),
        tables=(first, second),
        summary_cells=subtotals,
        segmentation_confidence=SegmentationConfidence.MEDIUM,
        segmentation_basis=("test",),
        allow_cross_row_checks=True,
    )

    relationships, suppressed = evaluate_logical_invoice(invoice)
    assert not any(rel.kind is RelationshipKind.SUBTOTAL_SUM for rel in relationships)
    assert not any(rel.kind is RelationshipKind.GRAND_TOTAL for rel in relationships)
    assert {check.kind for check in suppressed} >= {
        RelationshipKind.SUBTOTAL_SUM, RelationshipKind.GRAND_TOTAL
    }


def test_explicit_page_subtotals_can_reconcile_final_total():
    first = _table([_line_row(2, 100.0, 200.0, 480), _line_row(1, 50.0, 50.0, 456)])
    second = _on_page(
        _table([_line_row(3, 100.0, 300.0, 480), _line_row(1, 25.0, 25.0, 456)]),
        1,
    )
    summaries = (
        replace(_cell(ColumnRole.SUBTOTAL, 250.0, 300, 400), role_label="Page Subtotal"),
        replace(
            _cell(ColumnRole.SUBTOTAL, 325.0, 300, 400),
            page_index=1,
            role_label="Page Subtotal",
        ),
        replace(
            _cell(ColumnRole.GRAND_TOTAL, 575.0, 300, 360),
            page_index=1,
            role_label="Grand Total",
        ),
    )
    invoice = LogicalInvoice(
        logical_invoice_id="invoice-001",
        page_indexes=(0, 1),
        tables=(first, second),
        summary_cells=summaries,
        segmentation_confidence=SegmentationConfidence.MEDIUM,
        segmentation_basis=("test",),
        allow_cross_row_checks=True,
    )

    relationships, _ = evaluate_logical_invoice(invoice)
    subtotals = [rel for rel in relationships if rel.kind is RelationshipKind.SUBTOTAL_SUM]
    grand = [rel for rel in relationships if rel.kind is RelationshipKind.GRAND_TOTAL]
    assert len(subtotals) == 2
    assert all(rel.within_tolerance for rel in subtotals)
    assert len(grand) == 1 and grand[0].within_tolerance


# --------------------------------------------------------------------------- #
# Tamper localization + convergence (Step E)
# --------------------------------------------------------------------------- #

def test_convergence_amount_edit_breaks_line_and_subtotal():
    # Original: amounts 200,150,300; subtotal 650. The third amount is inflated
    # to 30000 but the printed subtotal stays 650. Correcting the 30000 cell
    # reconciles BOTH the line item AND the subtotal sum -> convergence 2.
    rows = [
        _line_row(2, 100.0, 200.0, 480),
        _line_row(3, 50.0, 150.0, 456),
        _line_row(1, 300.0, 30000.0, 432),
    ]
    table = _table(rows)
    summary = {ColumnRole.SUBTOTAL: _cell(ColumnRole.SUBTOTAL, 650.0, 300, 400)}
    rels = evaluate_relationships([table], summary)
    conv = annotate_convergence(rels)

    # Find the broken line-item relationship (the 30000 row).
    broken_line = [
        i for i, r in enumerate(rels)
        if r.kind is RelationshipKind.LINE_ITEM and not r.within_tolerance
    ]
    assert len(broken_line) == 1
    count, corroborators = conv[broken_line[0]]
    assert count == 2  # convergence: line item + subtotal sum
    assert any(c.kind is RelationshipKind.SUBTOTAL_SUM for c in corroborators)


def test_lone_break_has_no_convergence():
    # A single broken line item with no subtotal -> convergence 1 (could be noise).
    table = _table([
        _line_row(2, 100.0, 200.0, 480),
        _line_row(3, 83.23, 24019.69, 456),
    ])
    rels = evaluate_relationships([table])
    conv = annotate_convergence(rels)
    broken = [i for i, r in enumerate(rels) if not r.within_tolerance]
    assert len(broken) == 1
    count, corroborators = conv[broken[0]]
    assert count == 1
    assert corroborators == []


def test_convergence_never_crosses_logical_invoice_ids():
    table = _table([_line_row(3, 83.23, 24019.69, 456)])
    first = evaluate_relationships([table])[0]
    second = replace(first, logical_invoice_id="invoice-002")

    convergence = annotate_convergence([first, second])
    assert convergence[0] == (1, [])
    assert convergence[1] == (1, [])
