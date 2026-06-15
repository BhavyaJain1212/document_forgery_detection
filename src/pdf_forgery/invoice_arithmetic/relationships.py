"""Evaluate the labelled accounting relationships (Steps B–D).

We only evaluate relationships the labelled structure says SHOULD hold — never
brute-force every number pair (that manufactures coincidental matches AND
coincidental mismatches and false-alarms on honest bills). Each relationship is
compared with both an absolute and a relative tolerance so legitimate rounding
(``249.685`` shown as ``249.69``) does not flag.
"""

from __future__ import annotations

from .config import InvoiceConfig
from .models import (
    Cell,
    ColumnRole,
    LogicalInvoice,
    Relationship,
    RelationshipKind,
    Row,
    SegmentationConfidence,
    SuppressedCheck,
    Table,
)
from .numbers import normalize_label


def evaluate_relationships(
    tables: list[Table],
    summary: dict[ColumnRole, Cell] | None = None,
    config: InvoiceConfig | None = None,
) -> list[Relationship]:
    """Return every confidently-evaluable relationship across *tables*.

    *summary* is the role -> :class:`Cell` map of summary-block fields (subtotal,
    discount, taxes, grand total, deposit, balance) printed outside the line-item
    table; those drive the subtotal / GST / grand-total / deposit-balance checks.
    """
    cfg = config or InvoiceConfig()
    summary = summary or {}
    invoice = LogicalInvoice(
        logical_invoice_id="invoice-001",
        page_indexes=tuple(sorted({table.page_index for table in tables})),
        tables=tuple(tables),
        summary_cells=tuple(summary.values()),
        segmentation_confidence=SegmentationConfidence.HIGH,
        segmentation_basis=("compatibility wrapper: caller supplied one invoice",),
        allow_cross_row_checks=True,
    )
    rels, _ = evaluate_logical_invoice(invoice, cfg)
    return rels


def evaluate_logical_invoice(
    invoice: LogicalInvoice, config: InvoiceConfig | None = None
) -> tuple[list[Relationship], list[SuppressedCheck]]:
    """Evaluate one invoice; row equations always run, group equations are gated."""
    cfg = config or InvoiceConfig()
    rels: list[Relationship] = []
    suppressed: list[SuppressedCheck] = []
    for table in invoice.tables:
        if cfg.enable_line_item:
            rels.extend(_line_items(table, cfg, invoice))
        if cfg.enable_gst_split:
            rels.extend(_gst_sum(table, cfg, invoice))
        if cfg.enable_deposit_balance:
            rels.extend(_deposit_balance(table, cfg, invoice))

    if not invoice.allow_cross_row_checks:
        suppressed.extend(_suppressed_invoice_checks(invoice, cfg))
        return rels, suppressed

    by_role: dict[ColumnRole, list[Cell]] = {}
    for cell in invoice.summary_cells:
        by_role.setdefault(cell.role, []).append(cell)
    summary = invoice.summary_by_role()

    if cfg.enable_subtotal:
        subtotals = by_role.get(ColumnRole.SUBTOTAL, [])
        if len(subtotals) <= 1:
            rels.extend(_subtotal_sum(list(invoice.tables), summary, cfg, invoice))
        else:
            rels.extend(_page_subtotal_sums(invoice, subtotals, cfg))
            rels.extend(_page_subtotals_to_grand_total(invoice, by_role, cfg))
            suppressed.append(_suppressed(
                invoice,
                RelationshipKind.SUBTOTAL_SUM,
                "multiple subtotal fields are page-local; no unique invoice-wide subtotal",
            ))
    if cfg.enable_gst_split:
        rels.extend(_gst_sum_summary(summary, cfg, invoice))
    if cfg.enable_grand_total:
        rels.extend(_grand_total(summary, cfg, invoice))
    if cfg.enable_deposit_balance:
        rels.extend(_deposit_balance_summary(summary, cfg, invoice))
    suppressed.extend(_duplicate_role_suppressions(invoice, by_role, cfg))
    return rels, suppressed


def _suppressed_invoice_checks(
    invoice: LogicalInvoice, cfg: InvoiceConfig
) -> list[SuppressedCheck]:
    roles = {cell.role for cell in invoice.summary_cells}
    checks: list[SuppressedCheck] = []
    if cfg.enable_subtotal and ColumnRole.SUBTOTAL in roles:
        checks.append(_suppressed(
            invoice,
            RelationshipKind.SUBTOTAL_SUM,
            "invoice boundary is ambiguous; line amounts cannot be safely summed",
        ))
    if cfg.enable_grand_total and {
        ColumnRole.SUBTOTAL, ColumnRole.GRAND_TOTAL
    }.issubset(roles):
        checks.append(_suppressed(
            invoice,
            RelationshipKind.GRAND_TOTAL,
            "summary roles are not uniquely owned by a confident invoice group",
        ))
    if cfg.enable_gst_split and ColumnRole.GST in roles:
        checks.append(_suppressed(
            invoice,
            RelationshipKind.GST_SUM,
            "tax summary roles are not uniquely owned by a confident invoice group",
        ))
    if cfg.enable_deposit_balance and {
        ColumnRole.DEPOSIT, ColumnRole.BALANCE, ColumnRole.GRAND_TOTAL
    }.issubset(roles):
        checks.append(_suppressed(
            invoice,
            RelationshipKind.DEPOSIT_BALANCE,
            "deposit/balance summary roles are not uniquely owned by a confident invoice group",
        ))
    return checks


def _duplicate_role_suppressions(
    invoice: LogicalInvoice,
    by_role: dict[ColumnRole, list[Cell]],
    cfg: InvoiceConfig,
) -> list[SuppressedCheck]:
    duplicate = {role for role, cells in by_role.items() if len(cells) > 1}
    checks: list[SuppressedCheck] = []
    grand_duplicates = duplicate.intersection({
        ColumnRole.SUBTOTAL, ColumnRole.DISCOUNT, ColumnRole.CGST,
        ColumnRole.SGST, ColumnRole.IGST, ColumnRole.GST,
        ColumnRole.GRAND_TOTAL,
    })
    only_page_subtotals = grand_duplicates == {ColumnRole.SUBTOTAL} and not any(
        role in by_role
        for role in (
            ColumnRole.DISCOUNT, ColumnRole.CGST, ColumnRole.SGST,
            ColumnRole.IGST, ColumnRole.GST,
        )
    ) and len(by_role.get(ColumnRole.GRAND_TOTAL, [])) == 1 and all(
        _is_explicit_page_subtotal(cell)
        for cell in by_role.get(ColumnRole.SUBTOTAL, [])
    )
    if cfg.enable_grand_total and grand_duplicates and not only_page_subtotals:
        checks.append(_suppressed(
            invoice,
            RelationshipKind.GRAND_TOTAL,
            "grand-total inputs are not uniquely owned within the logical invoice",
        ))
    if cfg.enable_gst_split and duplicate.intersection({
        ColumnRole.CGST, ColumnRole.SGST, ColumnRole.IGST, ColumnRole.GST,
    }):
        checks.append(_suppressed(
            invoice,
            RelationshipKind.GST_SUM,
            "GST summary inputs are not uniquely owned within the logical invoice",
        ))
    if cfg.enable_deposit_balance and duplicate.intersection({
        ColumnRole.DEPOSIT, ColumnRole.BALANCE, ColumnRole.GRAND_TOTAL,
    }):
        checks.append(_suppressed(
            invoice,
            RelationshipKind.DEPOSIT_BALANCE,
            "deposit/balance inputs are not uniquely owned within the logical invoice",
        ))
    return checks


def _page_subtotal_sums(
    invoice: LogicalInvoice, subtotals: list[Cell], cfg: InvoiceConfig
) -> list[Relationship]:
    """Evaluate repeated subtotals only against rows on their own page."""
    rels: list[Relationship] = []
    if not all(_is_explicit_page_subtotal(cell) for cell in subtotals):
        return rels
    for subtotal in subtotals:
        amounts = _line_amount_cells([
            table for table in invoice.tables if table.page_index == subtotal.page_index
        ])
        if subtotal.value is None or len(amounts) < 2:
            continue
        expected = sum(cell.value for cell in amounts if cell.value is not None)
        eq = (
            f"sum(page {subtotal.page_index + 1} line amounts) = {_fmt(expected)}, "
            f"stated subtotal {_fmt(subtotal.value)}"
        )
        rels.append(_make_relationship(
            RelationshipKind.SUBTOTAL_SUM,
            subtotal.page_index,
            eq,
            expected,
            subtotal.value,
            output_cell=subtotal,
            input_cells=tuple(amounts),
            cfg=cfg,
            invoice=invoice,
        ))
    return rels


def _page_subtotals_to_grand_total(
    invoice: LogicalInvoice,
    by_role: dict[ColumnRole, list[Cell]],
    cfg: InvoiceConfig,
) -> list[Relationship]:
    """Sum page subtotals into one final total when no adjustments intervene."""
    subtotals = by_role.get(ColumnRole.SUBTOTAL, [])
    grands = by_role.get(ColumnRole.GRAND_TOTAL, [])
    adjusted = any(
        by_role.get(role)
        for role in (
            ColumnRole.DISCOUNT, ColumnRole.CGST, ColumnRole.SGST,
            ColumnRole.IGST, ColumnRole.GST,
        )
    )
    if (
        len(subtotals) < 2
        or len(grands) != 1
        or adjusted
        or not all(_is_explicit_page_subtotal(cell) for cell in subtotals)
    ):
        return []
    if len({cell.page_index for cell in subtotals}) != len(subtotals):
        return []
    grand = grands[0]
    if grand.value is None or any(cell.value is None for cell in subtotals):
        return []
    expected = sum(cell.value for cell in subtotals if cell.value is not None)
    eq = (
        f"sum(page subtotals) = {_fmt(expected)}, "
        f"stated grand total {_fmt(grand.value)}"
    )
    return [_make_relationship(
        RelationshipKind.GRAND_TOTAL,
        grand.page_index,
        eq,
        expected,
        grand.value,
        output_cell=grand,
        input_cells=tuple(subtotals),
        cfg=cfg,
        invoice=invoice,
    )]


def _is_explicit_page_subtotal(cell: Cell) -> bool:
    return "page" in normalize_label(cell.role_label)


def _suppressed(
    invoice: LogicalInvoice, kind: RelationshipKind, reason: str
) -> SuppressedCheck:
    return SuppressedCheck(
        kind=kind,
        logical_invoice_id=invoice.logical_invoice_id,
        page_indexes=invoice.page_indexes,
        reason=reason,
        segmentation_confidence=invoice.segmentation_confidence,
        segmentation_basis=invoice.segmentation_basis,
    )


# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------

def _within_tolerance(expected: float, stated: float, cfg: InvoiceConfig) -> bool:
    delta = abs(expected - stated)
    if delta <= cfg.abs_tolerance:
        return True
    return delta <= cfg.rel_tolerance * max(abs(expected), 1e-9)


def _is_gross(expected: float, stated: float, cfg: InvoiceConfig) -> bool:
    """A break far outside tolerance — not plausibly rounding/extraction noise."""
    rel = abs(expected - stated) / max(abs(expected), 1e-9)
    return rel >= cfg.gross_rel_error


def _make_relationship(
    kind: RelationshipKind,
    page_index: int,
    equation_text: str,
    expected: float,
    stated: float,
    output_cell: Cell | None,
    input_cells: tuple[Cell, ...],
    cfg: InvoiceConfig,
    invoice: LogicalInvoice,
) -> Relationship:
    within = _within_tolerance(expected, stated, cfg)
    return Relationship(
        kind=kind,
        page_index=page_index,
        equation_text=equation_text,
        expected=round(expected, 4),
        stated=stated,
        delta=round(stated - expected, 4),
        within_tolerance=within,
        is_gross=(not within) and _is_gross(expected, stated, cfg),
        output_cell=output_cell,
        input_cells=input_cells,
        logical_invoice_id=invoice.logical_invoice_id,
        segmentation_confidence=invoice.segmentation_confidence,
        segmentation_basis=invoice.segmentation_basis,
        role_label=(output_cell.role_label if output_cell is not None else ""),
        equation_kind=kind.value,
    )


# ---------------------------------------------------------------------------
# Relationship families
# ---------------------------------------------------------------------------

def _num(cell: Cell | None) -> float | None:
    return cell.value if cell is not None and cell.value is not None else None


def _line_items(
    table: Table, cfg: InvoiceConfig, invoice: LogicalInvoice
) -> list[Relationship]:
    """qty * rate = amount, per data row that confidently exposes all three."""
    if not (table.has_role(ColumnRole.RATE) and table.has_role(ColumnRole.AMOUNT)):
        return []
    if not table.has_role(ColumnRole.QTY):
        return []
    rels: list[Relationship] = []
    for row in table.rows:
        qty_c, rate_c, amt_c = (
            row.by_role(ColumnRole.QTY),
            row.by_role(ColumnRole.RATE),
            row.by_role(ColumnRole.AMOUNT),
        )
        qty, rate, amount = _num(qty_c), _num(rate_c), _num(amt_c)
        if qty is None or rate is None or amount is None:
            continue
        expected = qty * rate
        eq = (
            f"qty({_fmt(qty)}) * rate({_fmt(rate)}) = {_fmt(expected)}, "
            f"stated {_fmt(amount)}"
        )
        rels.append(
            _make_relationship(
                RelationshipKind.LINE_ITEM, table.page_index, eq, expected, amount,
                output_cell=amt_c, input_cells=(qty_c, rate_c), cfg=cfg,
                invoice=invoice,
            )
        )
    return rels


def _line_amount_cells(tables: list[Table]) -> list[Cell]:
    """Amount cells of GENUINE line-item rows across all tables (page order).

    A genuine line item has a numeric amount AND a numeric quantity or rate in
    the same row. This excludes summary-block rows (label + lone value) that a
    table whose body runs to the page bottom would otherwise sweep in, which
    would corrupt ``sum(line amounts)``.
    """
    cells: list[Cell] = []
    for table in tables:
        if not table.has_role(ColumnRole.AMOUNT):
            continue
        for row in table.rows:
            amt = row.by_role(ColumnRole.AMOUNT)
            if amt is None or amt.value is None:
                continue
            qty = row.by_role(ColumnRole.QTY)
            rate = row.by_role(ColumnRole.RATE)
            has_operand = (qty is not None and qty.value is not None) or (
                rate is not None and rate.value is not None
            )
            if has_operand:
                cells.append(amt)
    return cells


def _subtotal_sum(
    tables: list[Table], summary: dict[ColumnRole, Cell], cfg: InvoiceConfig,
    invoice: LogicalInvoice,
) -> list[Relationship]:
    """sum(line amounts) = subtotal, when a subtotal field is present.

    The subtotal cell is taken from the summary block (the usual place) or from a
    SUBTOTAL column inside a table.
    """
    subtotal_cell = summary.get(ColumnRole.SUBTOTAL)
    if subtotal_cell is None:
        for table in tables:
            for row in table.rows:
                sub = row.by_role(ColumnRole.SUBTOTAL)
                if sub is not None and sub.value is not None:
                    subtotal_cell = sub
                    break
            if subtotal_cell is not None:
                break
    if subtotal_cell is None or subtotal_cell.value is None:
        return []
    amounts = _line_amount_cells(tables)
    if len(amounts) < 2:
        return []
    expected = sum(c.value for c in amounts)  # type: ignore[misc]
    eq = (
        f"sum(line amounts) = {_fmt(expected)}, "
        f"stated subtotal {_fmt(subtotal_cell.value)}"
    )
    return [
        _make_relationship(
            RelationshipKind.SUBTOTAL_SUM, subtotal_cell.page_index, eq, expected,
            subtotal_cell.value, output_cell=subtotal_cell,
            input_cells=tuple(amounts), cfg=cfg, invoice=invoice,
        )
    ]


def _grand_total(
    summary: dict[ColumnRole, Cell], cfg: InvoiceConfig, invoice: LogicalInvoice
) -> list[Relationship]:
    """subtotal - discount + taxes = grand total (from the summary block)."""
    subtotal = summary.get(ColumnRole.SUBTOTAL)
    grand = summary.get(ColumnRole.GRAND_TOTAL)
    if subtotal is None or grand is None:
        return []
    if subtotal.value is None or grand.value is None:
        return []
    discount = summary.get(ColumnRole.DISCOUNT)
    taxes = [
        c for c in (
            summary.get(ColumnRole.CGST),
            summary.get(ColumnRole.SGST),
            summary.get(ColumnRole.IGST),
        ) if c is not None and c.value is not None
    ]
    if not taxes:
        gst = summary.get(ColumnRole.GST)
        if gst is not None and gst.value is not None:
            taxes = [gst]

    tax_total = sum(c.value for c in taxes)  # type: ignore[misc]
    disc_val = discount.value if discount is not None and discount.value is not None else 0.0
    expected = subtotal.value - disc_val + tax_total
    parts = [f"subtotal({_fmt(subtotal.value)})"]
    if discount is not None and discount.value:
        parts.append(f"- discount({_fmt(disc_val)})")
    if taxes:
        parts.append(f"+ tax({_fmt(tax_total)})")
    eq = f"{' '.join(parts)} = {_fmt(expected)}, stated grand total {_fmt(grand.value)}"
    inputs = tuple(c for c in ([subtotal, discount] + taxes) if c is not None)
    return [
        _make_relationship(
            RelationshipKind.GRAND_TOTAL, grand.page_index, eq, expected,
            grand.value, output_cell=grand, input_cells=inputs, cfg=cfg,
            invoice=invoice,
        )
    ]


def _gst_sum_summary(
    summary: dict[ColumnRole, Cell], cfg: InvoiceConfig, invoice: LogicalInvoice
) -> list[Relationship]:
    """CGST + SGST (+ IGST) = total GST, from the summary block."""
    gst = summary.get(ColumnRole.GST)
    if gst is None or gst.value is None:
        return []
    parts = [
        c for c in (
            summary.get(ColumnRole.CGST),
            summary.get(ColumnRole.SGST),
            summary.get(ColumnRole.IGST),
        ) if c is not None and c.value is not None
    ]
    if len(parts) < 2:
        return []
    expected = sum(c.value for c in parts)  # type: ignore[misc]
    eq = f"CGST/SGST/IGST sum = {_fmt(expected)}, stated total GST {_fmt(gst.value)}"
    return [
        _make_relationship(
            RelationshipKind.GST_SUM, gst.page_index, eq, expected, gst.value,
            output_cell=gst, input_cells=tuple(parts), cfg=cfg, invoice=invoice,
        )
    ]


def _deposit_balance_summary(
    summary: dict[ColumnRole, Cell], cfg: InvoiceConfig, invoice: LogicalInvoice
) -> list[Relationship]:
    """deposit + balance = grand total / final amount, from the summary block."""
    deposit = summary.get(ColumnRole.DEPOSIT)
    balance = summary.get(ColumnRole.BALANCE)
    total = summary.get(ColumnRole.GRAND_TOTAL)
    if deposit is None or balance is None or total is None:
        return []
    if deposit.value is None or balance.value is None or total.value is None:
        return []
    expected = deposit.value + balance.value
    eq = (
        f"deposit({_fmt(deposit.value)}) + balance({_fmt(balance.value)}) = "
        f"{_fmt(expected)}, stated {_fmt(total.value)}"
    )
    return [
        _make_relationship(
            RelationshipKind.DEPOSIT_BALANCE, total.page_index, eq, expected,
            total.value, output_cell=total, input_cells=(deposit, balance), cfg=cfg,
            invoice=invoice,
        )
    ]


def _gst_sum(
    table: Table, cfg: InvoiceConfig, invoice: LogicalInvoice
) -> list[Relationship]:
    """CGST + SGST (+ IGST) = total GST, when those cells coexist on a row."""
    rels: list[Relationship] = []
    for row in table.rows:
        cgst = _num(row.by_role(ColumnRole.CGST))
        sgst = _num(row.by_role(ColumnRole.SGST))
        igst = _num(row.by_role(ColumnRole.IGST))
        gst_cell = row.by_role(ColumnRole.GST)
        gst = _num(gst_cell)
        parts = [p for p in (cgst, sgst, igst) if p is not None]
        if gst is None or len(parts) < 2:
            continue
        expected = sum(parts)
        eq = f"CGST/SGST/IGST sum = {_fmt(expected)}, stated total GST {_fmt(gst)}"
        rels.append(
            _make_relationship(
                RelationshipKind.GST_SUM, table.page_index, eq, expected, gst,
                output_cell=gst_cell,
                input_cells=tuple(
                    c for c in (
                        row.by_role(ColumnRole.CGST),
                        row.by_role(ColumnRole.SGST),
                        row.by_role(ColumnRole.IGST),
                    ) if c is not None and c.value is not None
                ),
                cfg=cfg, invoice=invoice,
            )
        )
    return rels


def _deposit_balance(
    table: Table, cfg: InvoiceConfig, invoice: LogicalInvoice
) -> list[Relationship]:
    """deposit + balance = grand total / final amount, on a row that has all."""
    rels: list[Relationship] = []
    for row in table.rows:
        deposit = _num(row.by_role(ColumnRole.DEPOSIT))
        balance = _num(row.by_role(ColumnRole.BALANCE))
        total_cell = row.by_role(ColumnRole.GRAND_TOTAL) or row.by_role(ColumnRole.AMOUNT)
        total = _num(total_cell)
        if deposit is None or balance is None or total is None:
            continue
        expected = deposit + balance
        eq = (
            f"deposit({_fmt(deposit)}) + balance({_fmt(balance)}) = {_fmt(expected)}, "
            f"stated {_fmt(total)}"
        )
        rels.append(
            _make_relationship(
                RelationshipKind.DEPOSIT_BALANCE, table.page_index, eq, expected,
                total, output_cell=total_cell,
                input_cells=tuple(
                    c for c in (
                        row.by_role(ColumnRole.DEPOSIT),
                        row.by_role(ColumnRole.BALANCE),
                    ) if c is not None
                ),
                cfg=cfg, invoice=invoice,
            )
        )
    return rels


def _fmt(value: float) -> str:
    """Format a number for the equation text: drop trailing ``.0`` noise."""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"
