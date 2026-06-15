"""Stage — invoice arithmetic.

Reads the bill like an accountant and flags numbers whose labelled relationships
no longer hold. Reconstructs the table from glyph coordinates (row clustering on
y, column clustering on x, header-driven role assignment), parses numeric cells
robustly (Western + Indian grouping, currency symbols, decimals), then evaluates
only the relationships the labelled structure says SHOULD hold:

    qty * rate = amount, sum(amounts) = subtotal, subtotal + tax - discount =
    grand total, CGST + SGST (+ IGST) = total GST, deposit + balance = final.

Tolerance (absolute + relative) ignores legitimate rounding. Tamper localization
finds the single cell whose correction reconciles the MOST broken equations;
convergence of several broken equations on one cell raises confidence, a lone
break (which could be source/extraction error) is capped at strong MEDIUM.

Confidence (advisory, shared rubric): HIGH on convergence; strong MEDIUM for a
lone gross break; MEDIUM for a near-tolerance break; LOW when all reconcile;
INCONCLUSIVE when the table/roles cannot be identified (precision over recall).
"""

from .adapter import (
    STAGE_NAME,
    render_json,
    render_stage_json,
    render_stage_summary,
    render_summary,
    report_to_dict,
    report_to_stage_result,
    stage_result_to_report,
)
from .analyze import (
    analyze_bytes,
    analyze_bytes_as_stage,
    analyze_path,
    analyze_path_as_stage,
)
from .config import InvoiceConfig
from .detect import detect, detect_detailed
from .localize import annotate_convergence
from .models import (
    ArithmeticFinding,
    ArithmeticFindingKind,
    Cell,
    Column,
    ColumnRole,
    ConfidenceTier,
    HighValueKind,
    InvoiceReport,
    InvoiceDetectionResult,
    LogicalInvoice,
    Relationship,
    RelationshipKind,
    Row,
    Table,
    SegmentationConfidence,
    SuppressedCheck,
)
from .numbers import is_numeric_cell, normalize_label, parse_amount
from .relationships import evaluate_logical_invoice, evaluate_relationships
from .scoring import score
from .stage import InvoiceArithmeticStage
from .table import build_tables

__all__ = [
    # config
    "InvoiceConfig",
    # number parsing
    "parse_amount",
    "is_numeric_cell",
    "normalize_label",
    # table reconstruction
    "build_tables",
    # relationships / localization / detection / scoring
    "evaluate_relationships",
    "annotate_convergence",
    "detect",
    "detect_detailed",
    "evaluate_logical_invoice",
    "score",
    # orchestration
    "analyze_bytes",
    "analyze_path",
    "analyze_bytes_as_stage",
    "analyze_path_as_stage",
    # stage / adapter
    "InvoiceArithmeticStage",
    "STAGE_NAME",
    "report_to_stage_result",
    "stage_result_to_report",
    # rendering
    "render_json",
    "render_summary",
    "render_stage_json",
    "render_stage_summary",
    "report_to_dict",
    # models
    "InvoiceReport",
    "InvoiceDetectionResult",
    "LogicalInvoice",
    "SuppressedCheck",
    "SegmentationConfidence",
    "ArithmeticFinding",
    "ArithmeticFindingKind",
    "Relationship",
    "RelationshipKind",
    "Cell",
    "Row",
    "Column",
    "Table",
    "ColumnRole",
    "ConfidenceTier",
    "HighValueKind",
]
