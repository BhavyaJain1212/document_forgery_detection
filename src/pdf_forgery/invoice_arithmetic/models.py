"""Pure data models for the invoice-arithmetic stage.

Frozen dataclasses with no detection logic, mirroring the convention of
``revision_recovery.models`` / ``font_forensics.models``:

    table reconstruction -> :class:`Cell` / :class:`Row` / :class:`Table`
    relationship eval     -> :class:`Relationship`
    detection             -> :class:`ArithmeticFinding`
    aggregation           -> :class:`InvoiceReport`

The shared :class:`ConfidenceTier` and high-value :class:`HighValueKind` come
from ``core`` / ``revision_recovery`` so every stage speaks one vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..core.types import ConfidenceTier
from ..revision_recovery.models import HighValueKind

__all__ = [
    "ColumnRole",
    "RelationshipKind",
    "ArithmeticFindingKind",
    "SegmentationConfidence",
    "Cell",
    "Row",
    "Column",
    "Table",
    "LogicalInvoice",
    "SuppressedCheck",
    "InvoiceDetectionResult",
    "Relationship",
    "ArithmeticFinding",
    "InvoiceReport",
    "ConfidenceTier",
    "HighValueKind",
]


class ColumnRole(str, Enum):
    """The accounting meaning assigned to a table column from its header."""

    DESCRIPTION = "description"
    QTY = "qty"
    RATE = "rate"
    AMOUNT = "amount"
    SUBTOTAL = "subtotal"
    DISCOUNT = "discount"
    CGST = "cgst"
    SGST = "sgst"
    IGST = "igst"
    GST = "gst"
    GRAND_TOTAL = "grand_total"
    DEPOSIT = "deposit"
    BALANCE = "balance"
    DATE = "date"
    UNKNOWN = "unknown"


class RelationshipKind(str, Enum):
    """Which accounting identity a :class:`Relationship` checks."""

    LINE_ITEM = "line_item"            # qty * rate = amount
    SUBTOTAL_SUM = "subtotal_sum"      # sum(line amounts) = subtotal
    GRAND_TOTAL = "grand_total"        # subtotal + tax - discount = grand total
    GST_SUM = "gst_sum"                # cgst + sgst (+ igst) = total gst
    DEPOSIT_BALANCE = "deposit_balance"  # deposit + balance = final amount
    ROOM_CHARGE = "room_charge"        # room rate * days = room charge
    DATE_SPAN = "date_span"            # discharge - admission = charged days


class ArithmeticFindingKind(str, Enum):
    """Why an arithmetic finding was raised."""

    BROKEN_RELATIONSHIP = "broken_relationship"
    """A labelled relationship does not hold within tolerance."""


class SegmentationConfidence(str, Enum):
    """Confidence that pages/tables belong to one logical invoice."""

    HIGH = "high"
    MEDIUM = "medium"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class Cell:
    """One parsed table cell with its geometry and (if numeric) value."""

    text: str
    value: float | None
    role: ColumnRole
    page_index: int
    bbox: tuple[float, float, float, float]
    """``(x0, y0, x1, y1)`` in pdfminer/PDF user space (origin bottom-left)."""
    role_label: str = ""
    """Original printed header/summary label that assigned :attr:`role`."""

    @property
    def is_numeric(self) -> bool:
        return self.value is not None

    def identity(self) -> tuple[int, int, int, int, int]:
        """A hashable identity for convergence bookkeeping (page + rounded bbox)."""
        return (self.page_index, *(round(v) for v in self.bbox))


@dataclass(frozen=True)
class Row:
    """A horizontal band of cells sharing a baseline."""

    page_index: int
    y: float
    cells: tuple[Cell, ...]

    def by_role(self, role: ColumnRole) -> Cell | None:
        for c in self.cells:
            if c.role is role:
                return c
        return None


@dataclass(frozen=True)
class Column:
    """A header-defined column: its role and horizontal centre."""

    role: ColumnRole
    label: str
    center: float
    x0: float
    x1: float


@dataclass(frozen=True)
class Table:
    """A reconstructed table: its header columns and data rows on one page."""

    page_index: int
    columns: tuple[Column, ...]
    rows: tuple[Row, ...]
    header_y: float
    source: str = "header"
    """``header`` for explicit tables, ``inferred`` for headerless continuations."""

    def has_role(self, role: ColumnRole) -> bool:
        return any(c.role is role for c in self.columns)

    @property
    def role_signature(self) -> tuple[ColumnRole, ...]:
        return tuple(c.role for c in self.columns if c.role is not ColumnRole.UNKNOWN)


@dataclass(frozen=True)
class LogicalInvoice:
    """A conservatively bounded invoice inside a possibly bundled PDF."""

    logical_invoice_id: str
    page_indexes: tuple[int, ...]
    tables: tuple[Table, ...]
    summary_cells: tuple[Cell, ...]
    segmentation_confidence: SegmentationConfidence
    segmentation_basis: tuple[str, ...]
    allow_cross_row_checks: bool

    @property
    def page_numbers(self) -> tuple[int, ...]:
        return tuple(page + 1 for page in self.page_indexes)

    def summary_by_role(self) -> dict[ColumnRole, Cell]:
        """Return only summary roles that are uniquely owned by this invoice."""
        by_role: dict[ColumnRole, list[Cell]] = {}
        for cell in self.summary_cells:
            by_role.setdefault(cell.role, []).append(cell)
        return {role: cells[0] for role, cells in by_role.items() if len(cells) == 1}


@dataclass(frozen=True)
class SuppressedCheck:
    """An invoice-level equation skipped because ownership was not reliable."""

    kind: RelationshipKind
    logical_invoice_id: str
    page_indexes: tuple[int, ...]
    reason: str
    segmentation_confidence: SegmentationConfidence
    segmentation_basis: tuple[str, ...]

    @property
    def page_numbers(self) -> tuple[int, ...]:
        return tuple(page + 1 for page in self.page_indexes)


@dataclass(frozen=True)
class Relationship:
    """One evaluated accounting identity over specific cells."""

    kind: RelationshipKind
    page_index: int
    equation_text: str
    """Human words, e.g. ``"qty(3.00) * rate(83.23) = 249.69, stated 24019.69"``."""

    expected: float
    stated: float
    delta: float
    within_tolerance: bool
    is_gross: bool
    output_cell: Cell | None
    """The cell holding the stated value (the conventional tamper target)."""

    input_cells: tuple[Cell, ...] = ()
    logical_invoice_id: str = "invoice-001"
    segmentation_confidence: SegmentationConfidence = SegmentationConfidence.HIGH
    segmentation_basis: tuple[str, ...] = ("compatibility wrapper",)
    role_label: str = ""
    equation_kind: str = ""

    @property
    def reconciled(self) -> bool:
        return self.within_tolerance

    @property
    def page_number(self) -> int:
        return self.page_index + 1

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        if self.output_cell is None:
            return (0.0, 0.0, 0.0, 0.0)
        return self.output_cell.bbox


@dataclass(frozen=True)
class ArithmeticFinding:
    """One flagged broken relationship, with everything a reviewer needs."""

    page_index: int
    kind: ArithmeticFindingKind
    tier: ConfidenceTier
    relationship_kind: RelationshipKind
    equation_text: str
    expected: float
    stated: float
    delta: float
    is_gross: bool
    reason: str

    # --- localized cell ---
    cell_role: ColumnRole = ColumnRole.UNKNOWN
    cell_text: str = ""
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    high_value: HighValueKind | None = None

    # --- convergence ---
    convergence_count: int = 1
    """How many independent broken equations this finding's localized cell would
    reconcile if corrected (>=2 means convergence -> HIGH-eligible)."""

    corroborating: tuple[str, ...] = ()
    """Equation texts of the OTHER broken relationships that converge on the
    same cell."""

    logical_invoice_id: str = "invoice-001"
    segmentation_confidence: SegmentationConfidence = SegmentationConfidence.HIGH
    segmentation_basis: tuple[str, ...] = ("compatibility wrapper",)
    role_label: str = ""
    equation_kind: str = ""

    @property
    def page_number(self) -> int:
        return self.page_index + 1


@dataclass(frozen=True)
class InvoiceDetectionResult:
    """Detailed detector result used internally while ``detect`` stays compatible."""

    findings: tuple[ArithmeticFinding, ...]
    relationships: tuple[Relationship, ...]
    tables: tuple[Table, ...]
    logical_invoices: tuple[LogicalInvoice, ...]
    suppressed_checks: tuple[SuppressedCheck, ...]


@dataclass(frozen=True)
class InvoiceReport:
    """Per-file result of the invoice-arithmetic stage (rich, stage-native)."""

    path: str
    ok: bool
    tier: ConfidenceTier
    score: int | None
    page_count: int = 0
    table_found: bool = False
    numeric_cell_count: int = 0
    relationships: tuple[Relationship, ...] = ()
    findings: tuple[ArithmeticFinding, ...] = ()
    logical_invoices: tuple[LogicalInvoice, ...] = ()
    suppressed_checks: tuple[SuppressedCheck, ...] = ()
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    error: str | None = None
    raw_size: int = 0
    page_dims: tuple[tuple[float, float], ...] = ()
    """Per-page ``(width_pt, height_pt)`` in PDF user space (points), indexed by
    ``page_index``.  Populated by ``analyze_bytes`` when layouts are available;
    used by the aggregate layer to normalize native bboxes into canonical form."""
    page_rotations: tuple[int, ...] = ()
    """Per-page ``/Rotate`` values (0/90/180/270), indexed by ``page_index``."""
    _tables: tuple[Table, ...] = field(default=(), repr=False, compare=False)
    """Reconstructed tables retained for inspection/tests."""
