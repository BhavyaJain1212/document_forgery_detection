"""Scoring rule tree: INCONCLUSIVE / LOW / MEDIUM / HIGH boundaries."""

from __future__ import annotations

from pdf_forgery.invoice_arithmetic.config import InvoiceConfig
from pdf_forgery.invoice_arithmetic.models import (
    ArithmeticFinding,
    ArithmeticFindingKind,
    ColumnRole,
    ConfidenceTier,
    HighValueKind,
    Relationship,
    RelationshipKind,
)
from pdf_forgery.invoice_arithmetic.scoring import score


def _rel(ok: bool) -> Relationship:
    return Relationship(
        kind=RelationshipKind.LINE_ITEM, page_index=0,
        equation_text="x", expected=1.0, stated=1.0, delta=0.0,
        within_tolerance=ok, is_gross=False, output_cell=None,
    )


def _finding(tier, *, gross=True, conv=1, hv=HighValueKind.AMOUNT) -> ArithmeticFinding:
    return ArithmeticFinding(
        page_index=0, kind=ArithmeticFindingKind.BROKEN_RELATIONSHIP, tier=tier,
        relationship_kind=RelationshipKind.LINE_ITEM, equation_text="x",
        expected=1.0, stated=2.0, delta=1.0, is_gross=gross, reason="r",
        cell_role=ColumnRole.AMOUNT, high_value=hv, convergence_count=conv,
    )


def test_inconclusive_when_no_table():
    tier, sc, _ = score([], [], numeric_cell_count=0, table_found=False)
    assert tier is ConfidenceTier.INCONCLUSIVE
    assert sc is None


def test_inconclusive_when_too_few_numeric_cells():
    tier, _, _ = score([], [_rel(True)], numeric_cell_count=1, table_found=True)
    assert tier is ConfidenceTier.INCONCLUSIVE


def test_low_when_all_reconcile():
    rels = [_rel(True), _rel(True), _rel(True), _rel(True)]
    tier, sc, _ = score([], rels, numeric_cell_count=8, table_found=True)
    assert tier is ConfidenceTier.LOW
    assert sc == InvoiceConfig().score_low_reconciled


def test_medium_lone_gross_break_strong():
    finding = _finding(ConfidenceTier.MEDIUM, gross=True, conv=1)
    tier, sc, _ = score([finding], [_rel(False)] + [_rel(True)] * 3,
                        numeric_cell_count=8, table_found=True)
    assert tier is ConfidenceTier.MEDIUM
    assert sc == InvoiceConfig().score_medium_lone_gross


def test_medium_lone_minor_break():
    finding = _finding(ConfidenceTier.MEDIUM, gross=False, conv=1)
    tier, sc, _ = score([finding], [_rel(False)] + [_rel(True)] * 3,
                        numeric_cell_count=8, table_found=True)
    assert tier is ConfidenceTier.MEDIUM
    assert sc == InvoiceConfig().score_medium_lone_minor


def test_high_on_convergence_amount():
    finding = _finding(ConfidenceTier.HIGH, conv=2, hv=HighValueKind.AMOUNT)
    tier, sc, reasons = score([finding], [_rel(False)] * 2 + [_rel(True)] * 2,
                              numeric_cell_count=8, table_found=True)
    assert tier is ConfidenceTier.HIGH
    assert sc == InvoiceConfig().score_high_amount_convergence
    assert any("high-value" in r for r in reasons)


def test_high_score_lower_for_non_high_value():
    finding = _finding(ConfidenceTier.HIGH, conv=2, hv=None)
    tier, sc, _ = score([finding], [_rel(False)] * 2 + [_rel(True)] * 2,
                        numeric_cell_count=8, table_found=True)
    assert tier is ConfidenceTier.HIGH
    assert sc == InvoiceConfig().score_high_convergence


def test_high_requires_convergence_when_gated():
    # A lone gross break (no convergence) must NOT reach HIGH under the gate.
    cfg = InvoiceConfig(require_convergence_for_high=True)
    finding = _finding(ConfidenceTier.MEDIUM, gross=True, conv=1)
    tier, _, _ = score([finding], [_rel(False)] + [_rel(True)] * 3,
                       numeric_cell_count=8, table_found=True, config=cfg)
    assert tier is ConfidenceTier.MEDIUM
