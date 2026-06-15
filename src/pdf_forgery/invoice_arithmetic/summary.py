"""Extract a bill's summary block: ``label: value`` lines outside the table.

Subtotal / discount / CGST / SGST / IGST / total GST / grand total / deposit /
balance are usually printed as a stack of ``label   value`` lines BELOW the line
-item table, not as columns inside it. The table reconstructor (header-driven)
does not capture those, so we scan every line for the shape

    <one or more label tokens that normalise to a known role>  <one number>

and record every located value. The compatibility wrapper still returns the
first value per role, while invoice-aware evaluation owns all occurrences.
subtotal / GST / grand-total / deposit-balance relationships.
"""

from __future__ import annotations

from ..core.glyphs import Glyph, TextLine, group_lines
from .config import InvoiceConfig
from .models import Cell, ColumnRole
from .numbers import normalize_label, parse_amount
from .table import _label_to_role


def build_summary_fields(
    glyphs: list[Glyph], config: InvoiceConfig | None = None
) -> dict[ColumnRole, Cell]:
    """Compatibility wrapper returning the first summary cell for each role."""
    fields: dict[ColumnRole, Cell] = {}
    for cell in build_summary_cells(glyphs, config):
        fields.setdefault(cell.role, cell)
    return fields


def build_summary_cells(
    glyphs: list[Glyph], config: InvoiceConfig | None = None
) -> list[Cell]:
    """Return every located summary cell in document/page order."""
    cfg = config or InvoiceConfig()
    lines = group_lines(
        glyphs,
        line_baseline_tolerance=cfg.line_baseline_tolerance,
        token_gap_ratio=cfg.token_gap_ratio,
    )
    label_to_role = _label_to_role(cfg)

    cells: list[Cell] = []
    for line in lines:
        parsed = _line_label_value(line, label_to_role)
        if parsed is None:
            continue
        _, cell = parsed
        cells.append(cell)
    return cells


# Roles that may legitimately appear as a summary ``label: value`` line.
_SUMMARY_ROLES = {
    ColumnRole.SUBTOTAL,
    ColumnRole.DISCOUNT,
    ColumnRole.CGST,
    ColumnRole.SGST,
    ColumnRole.IGST,
    ColumnRole.GST,
    ColumnRole.GRAND_TOTAL,
    ColumnRole.DEPOSIT,
    ColumnRole.BALANCE,
}


def _line_label_value(
    line: TextLine, label_to_role: dict[str, ColumnRole]
) -> tuple[ColumnRole, Cell] | None:
    """A line of the form ``<role label words> <single number>`` -> (role, cell)."""
    numeric_tokens = [t for t in line.tokens if parse_amount(t.text) is not None]
    label_tokens = [t for t in line.tokens if parse_amount(t.text) is None]
    if len(numeric_tokens) != 1 or not label_tokens:
        return None

    # The label is the concatenation of the non-numeric tokens, normalised.
    original_label = " ".join(t.text for t in label_tokens)
    label = "".join(normalize_label(t.text) for t in label_tokens)
    role = label_to_role.get(label)
    if role is None or role not in _SUMMARY_ROLES:
        return None

    value = parse_amount(numeric_tokens[0].text)
    bbox = numeric_tokens[0].bbox
    cell = Cell(
        text=numeric_tokens[0].text,
        value=value,
        role=role,
        page_index=line.page_index,
        bbox=bbox,
        role_label=original_label,
    )
    return role, cell
