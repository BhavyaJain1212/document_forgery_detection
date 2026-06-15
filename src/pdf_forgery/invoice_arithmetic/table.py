"""Reconstruct invoice tables from glyph coordinates (Step A).

We do NOT trust pdfminer's linear text order. Instead we take the shared
per-character glyph extractor's line/token grouping (rows already clustered by
baseline, cells split on spaces/wide gaps) and then:

  1. find the header row — the line whose cells map to the most known column
     roles (Description / Qty / Rate / Amount / ...);
  2. turn each header cell into a :class:`Column` with a role and an x-centre;
  3. assign every later row's cells to a column by nearest midpoint boundary,
     parsing numeric cells robustly;
  4. emit one :class:`Table` per header (rows run until the next header / page).

A multi-line wrapped description simply yields rows with no numeric cells in the
numeric columns; relationship evaluation skips those.
"""

from __future__ import annotations

from ..core.glyphs import Glyph, TextLine, Token, group_lines
from .config import InvoiceConfig
from .models import Cell, Column, ColumnRole, Row, Table
from .numbers import normalize_label, parse_amount


def build_tables(glyphs: list[Glyph], config: InvoiceConfig | None = None) -> list[Table]:
    """Reconstruct all tables across all pages from *glyphs*."""
    cfg = config or InvoiceConfig()
    lines = group_lines(
        glyphs,
        line_baseline_tolerance=cfg.line_baseline_tolerance,
        token_gap_ratio=cfg.token_gap_ratio,
    )
    return build_tables_from_lines(lines, cfg)


def build_tables_from_lines(
    lines: list[TextLine], config: InvoiceConfig | None = None
) -> list[Table]:
    """Reconstruct explicit-header tables from already-grouped text lines."""
    cfg = config or InvoiceConfig()
    label_to_role = _label_to_role(cfg)

    by_page: dict[int, list[TextLine]] = {}
    for ln in lines:
        by_page.setdefault(ln.page_index, []).append(ln)

    tables: list[Table] = []
    for page_index in sorted(by_page):
        tables.extend(_tables_on_page(by_page[page_index], label_to_role, cfg))
    return tables


def schemas_align(a: Table, b: Table, config: InvoiceConfig | None = None) -> bool:
    """Whether two explicit/inferred tables have compatible roles and columns."""
    cfg = config or InvoiceConfig()
    a_cols = [c for c in a.columns if c.role is not ColumnRole.UNKNOWN]
    b_cols = [c for c in b.columns if c.role is not ColumnRole.UNKNOWN]
    if [c.role for c in a_cols] != [c.role for c in b_cols]:
        return False
    return all(
        abs(left.center - right.center) <= cfg.continuation_column_tolerance
        for left, right in zip(a_cols, b_cols)
    )


def infer_continuation_table(
    lines: list[TextLine], prior: Table, config: InvoiceConfig | None = None
) -> Table | None:
    """Map headerless page lines to a prior schema without guessing new roles."""
    if not lines:
        return None
    cfg = config or InvoiceConfig()
    columns = list(prior.columns)
    rows = [_row_from_line(line, columns, cfg) for line in lines]
    rows = [row for row in rows if _has_line_item_triple(row)]
    if not rows:
        return None
    return Table(
        page_index=lines[0].page_index,
        columns=prior.columns,
        rows=tuple(rows),
        header_y=max((line.glyphs[0].y0 for line in lines if line.glyphs), default=0.0),
        source="inferred",
    )


def valid_line_item_rows(table: Table) -> int:
    """Count rows with numeric qty, rate, and amount cells."""
    return sum(1 for row in table.rows if _has_line_item_triple(row))


# ---------------------------------------------------------------------------
# Header / role identification
# ---------------------------------------------------------------------------

def _label_to_role(cfg: InvoiceConfig) -> dict[str, ColumnRole]:
    """Flatten the config's role -> labels map into label -> role."""
    out: dict[str, ColumnRole] = {}
    for role_name, labels in cfg.role_labels.items():
        try:
            role = ColumnRole(role_name)
        except ValueError:  # a config role with no enum member -> ignore
            continue
        for label in labels:
            out.setdefault(normalize_label(label), role)
    return out


def _header_role_hits(line: TextLine, label_to_role: dict[str, ColumnRole]) -> int:
    """How many of *line*'s cells map to a known column role."""
    hits = 0
    for token in line.tokens:
        if label_to_role.get(normalize_label(token.text)) is not None:
            hits += 1
    return hits


def _tables_on_page(
    lines: list[TextLine],
    label_to_role: dict[str, ColumnRole],
    cfg: InvoiceConfig,
) -> list[Table]:
    # Lines come top-to-bottom from the shared extractor.
    header_indexes = [
        i
        for i, ln in enumerate(lines)
        if _header_role_hits(ln, label_to_role) >= cfg.min_header_role_matches
        and _is_numeric_table_header(ln, label_to_role)
    ]
    if not header_indexes:
        return []

    tables: list[Table] = []
    for h, start in enumerate(header_indexes):
        end = header_indexes[h + 1] if h + 1 < len(header_indexes) else len(lines)
        header_line = lines[start]
        columns = _columns_from_header(header_line, label_to_role)
        if not columns:
            continue
        rows = [
            _row_from_line(lines[j], columns, cfg)
            for j in range(start + 1, end)
        ]
        rows = [r for r in rows if r.cells]
        tables.append(
            Table(
                page_index=header_line.page_index,
                columns=tuple(columns),
                rows=tuple(rows),
                header_y=header_line.glyphs[0].y0 if header_line.glyphs else 0.0,
            )
        )
    return tables


def _is_numeric_table_header(line: TextLine, label_to_role: dict[str, ColumnRole]) -> bool:
    """A header must promise at least one numeric/value column to be useful."""
    numeric_roles = {
        ColumnRole.QTY, ColumnRole.RATE, ColumnRole.AMOUNT, ColumnRole.SUBTOTAL,
        ColumnRole.GRAND_TOTAL, ColumnRole.GST, ColumnRole.CGST, ColumnRole.SGST,
        ColumnRole.IGST, ColumnRole.DEPOSIT, ColumnRole.BALANCE, ColumnRole.DISCOUNT,
    }
    for token in line.tokens:
        role = label_to_role.get(normalize_label(token.text))
        if role in numeric_roles:
            return True
    return False


def _columns_from_header(
    header: TextLine, label_to_role: dict[str, ColumnRole]
) -> list[Column]:
    """Turn header cells into columns with roles + x-centres (ordered by x)."""
    cols: list[Column] = []
    for token in header.tokens:
        role = label_to_role.get(normalize_label(token.text), ColumnRole.UNKNOWN)
        x0, _, x1, _ = token.bbox
        cols.append(
            Column(role=role, label=token.text, center=(x0 + x1) / 2.0, x0=x0, x1=x1)
        )
    cols.sort(key=lambda c: c.center)
    return cols


# ---------------------------------------------------------------------------
# Row -> cells (column assignment)
# ---------------------------------------------------------------------------

def _column_boundaries(columns: list[Column], tol: float) -> list[float]:
    """Midpoint boundaries between adjacent column centres.

    Returns ``len(columns) - 1`` cut points; a cell at centre ``c`` belongs to
    column ``i`` where ``boundaries[i-1] <= c < boundaries[i]``.
    """
    bounds: list[float] = []
    for a, b in zip(columns, columns[1:]):
        bounds.append((a.center + b.center) / 2.0)
    return bounds


def _assign_column(center: float, columns: list[Column], boundaries: list[float]) -> int:
    """Index of the column whose midpoint band contains *center*."""
    for i, cut in enumerate(boundaries):
        if center < cut:
            return i
    return len(columns) - 1


def _row_from_line(line: TextLine, columns: list[Column], cfg: InvoiceConfig) -> Row:
    """Assign a line's tokens to columns, parsing numeric cells."""
    boundaries = _column_boundaries(columns, cfg.column_assign_tolerance)
    # Collect tokens per column; multiple tokens in one column are joined.
    buckets: dict[int, list[Token]] = {}
    for token in line.tokens:
        x0, _, x1, _ = token.bbox
        center = (x0 + x1) / 2.0
        idx = _assign_column(center, columns, boundaries)
        buckets.setdefault(idx, []).append(token)

    cells: list[Cell] = []
    for idx, tokens in sorted(buckets.items()):
        tokens.sort(key=lambda t: t.bbox[0])
        text = " ".join(t.text for t in tokens)
        # For value parsing, prefer a single numeric token in the bucket so a
        # stray adjacent label does not poison the parse.
        value = _bucket_value(tokens)
        xs0 = [t.bbox[0] for t in tokens]
        ys0 = [t.bbox[1] for t in tokens]
        xs1 = [t.bbox[2] for t in tokens]
        ys1 = [t.bbox[3] for t in tokens]
        cells.append(
            Cell(
                text=text,
                value=value,
                role=columns[idx].role,
                page_index=line.page_index,
                bbox=(min(xs0), min(ys0), max(xs1), max(ys1)),
                role_label=columns[idx].label,
            )
        )
    y = line.glyphs[0].y0 if line.glyphs else 0.0
    return Row(page_index=line.page_index, y=y, cells=tuple(cells))


def _bucket_value(tokens: list[Token]) -> float | None:
    """Parse the numeric value of a column bucket.

    A bucket parses to a number only when exactly one of its tokens is numeric
    (the common case: one number per cell). If a description token leaked into a
    numeric column we still recover the lone number; if two numbers collide we
    decline (ambiguous) rather than guess.
    """
    numeric = [parse_amount(t.text) for t in tokens]
    nums = [v for v in numeric if v is not None]
    if len(nums) == 1:
        return nums[0]
    if len(tokens) == 1:
        return numeric[0]
    return None


def _has_line_item_triple(row: Row) -> bool:
    return all(
        (cell := row.by_role(role)) is not None and cell.value is not None
        for role in (ColumnRole.QTY, ColumnRole.RATE, ColumnRole.AMOUNT)
    )
