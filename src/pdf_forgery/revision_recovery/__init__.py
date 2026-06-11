"""Stage 1 — revision recovery.

Detects direct-text-editing forgery by recovering the historical revisions an
incremental update leaves inside a PDF and proving what text changed.

Implemented so far:
    - detect: enumerate candidate revision boundaries from raw bytes.
    - reconstruct: truncate + load-validate each valid boundary with pikepdf.
    - extract: per-page text (pdfminer) + word boxes (pdfplumber) + normalize.
    - diff.textdiff: token-level text diff + char detail + high-value detection.
    - diff.objectdiff: changed-object detection + object classification.
    - highvalue: classify_token / classify_change (amount/date/id_like).

Proposed / not yet implemented (see ../../CLAUDE.md): config, scoring, report,
cli.
"""

from .detect import detect, detect_from_path
from .diff.objectdiff import classify_changed_object, diff_objects
from .diff.textdiff import diff_normalized_pages, diff_text
from .highvalue import classify_change, classify_token
from .models import (
    CharSpan,
    DetectionResult,
    EOFMarker,
    HighValueKind,
    ObjectChange,
    ObjectChangeClass,
    ObjectDiff,
    PageTextDiff,
    ReconstructionFailure,
    ReconstructionResult,
    Revision,
    RevisionBoundary,
    TextChange,
    TokenDiff,
    XrefSection,
)
from .reconstruct import reconstruct, reconstruct_from_path

__all__ = [
    # detection
    "detect",
    "detect_from_path",
    # reconstruction
    "reconstruct",
    "reconstruct_from_path",
    # text diff
    "diff_text",
    "diff_normalized_pages",
    # object diff
    "classify_changed_object",
    "diff_objects",
    # high-value classification
    "classify_token",
    "classify_change",
    # models
    "CharSpan",
    "DetectionResult",
    "EOFMarker",
    "HighValueKind",
    "ObjectChange",
    "ObjectChangeClass",
    "ObjectDiff",
    "PageTextDiff",
    "RevisionBoundary",
    "XrefSection",
    "Revision",
    "ReconstructionFailure",
    "ReconstructionResult",
    "TextChange",
    "TokenDiff",
]
