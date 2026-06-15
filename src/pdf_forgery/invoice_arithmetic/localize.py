"""Tamper localization + convergence (Step E).

When relationships break, several often break on the SAME cell: a forged line
amount makes ``qty*rate`` break AND ``sum=subtotal`` break AND
``subtotal+tax=grand total`` break. We compute, for each broken relationship,
the single cell whose correction would reconcile the MOST broken equations.

Convergence of multiple independent broken equations on one cell raises
confidence (-> HIGH eligible); a lone broken equation that could be extraction
noise stays low (-> MEDIUM ceiling). This module only measures convergence; the
scorer turns it into a tier.
"""

from __future__ import annotations

from .config import InvoiceConfig
from .models import Cell, Relationship


def _recompute_with(rel: Relationship, cell: Cell, new_value: float) -> bool:
    """Would *rel* reconcile if *cell* held *new_value* instead of its value?

    Only the arithmetic of *rel* is re-evaluated; other cells keep their stated
    values. A cell participates in *rel* either as the stated output or as one of
    the summed/multiplied inputs.
    """
    cid = cell.identity()
    abs_tol = 0.05  # recomputation uses a fixed small epsilon; tolerance config
    # is applied by the relationship itself for the headline verdict.

    # Output side: if the corrected cell IS the stated output, compare expected
    # vs the new stated value.
    if rel.output_cell is not None and rel.output_cell.identity() == cid:
        return abs(rel.expected - new_value) <= max(abs_tol, 1e-6 + 0.005 * abs(rel.expected))

    # Input side: rebuild ``expected`` with the corrected input, compare to the
    # unchanged stated output.
    inputs = list(rel.input_cells)
    if not any(c.identity() == cid for c in inputs):
        return False
    values = [new_value if c.identity() == cid else (c.value or 0.0) for c in inputs]
    from .models import RelationshipKind

    if rel.kind is RelationshipKind.LINE_ITEM:
        if len(values) != 2:
            return False
        expected = values[0] * values[1]
    else:  # all other modelled relationships are additive over their inputs
        expected = sum(values)
    return abs(expected - rel.stated) <= max(abs_tol, 0.005 * abs(rel.stated))


def annotate_convergence(
    relationships: list[Relationship], config: InvoiceConfig | None = None
) -> dict[int, tuple[int, list[Relationship]]]:
    """Map each broken relationship (by index) to ``(convergence, corroborators)``.

    For the broken relationship's localized cell (its stated output — the
    conventional tamper target), we ask which OTHER broken relationships would
    also reconcile if that same cell were corrected to this relationship's
    implied value. The count (including the relationship itself) is the
    convergence; the others are the corroborators.
    """
    cfg = config or InvoiceConfig()
    broken = [(i, r) for i, r in enumerate(relationships) if not r.within_tolerance]
    out: dict[int, tuple[int, list[Relationship]]] = {}

    for i, rel in broken:
        cell = rel.output_cell
        if cell is None:
            out[i] = (1, [])
            continue
        implied = rel.expected  # value the cell SHOULD hold per this equation
        corroborators: list[Relationship] = []
        for j, other in broken:
            if j == i:
                continue
            if other.logical_invoice_id != rel.logical_invoice_id:
                continue
            if _recompute_with(other, cell, implied):
                corroborators.append(other)
        out[i] = (1 + len(corroborators), corroborators)
    return out
