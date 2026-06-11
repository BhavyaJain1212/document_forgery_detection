"""Stage 1 — revision recovery.

Detects direct-text-editing forgery by recovering the historical revisions an
incremental update leaves inside a PDF and proving what text changed.

Implemented so far:
    - detect: enumerate candidate revision boundaries from raw bytes.
    - reconstruct: truncate + load-validate each valid boundary with pikepdf.
    - extract: per-page text (pdfminer) + word boxes (pdfplumber) + normalize.
    - diff.textdiff: token-level text diff + char detail + high-value detection.
    - highvalue: classify_token / classify_change (amount/date/id_like).

Proposed / not yet implemented (see ../../CLAUDE.md): diff.objectdiff,
config, scoring, report, cli.
"""

from .detect import detect, detect_from_path
from .diff.textdiff import diff_normalized_pages, diff_text
from .highvalue import classify_change, classify_token
from .models import (
    CharSpan,
    DetectionResult,
    EOFMarker,
    HighValueKind,
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
    # high-value classification
    "classify_token",
    "classify_change",
    # models
    "CharSpan",
    "DetectionResult",
    "EOFMarker",
    "HighValueKind",
    "PageTextDiff",
    "RevisionBoundary",
    "XrefSection",
    "Revision",
    "ReconstructionFailure",
    "ReconstructionResult",
    "TextChange",
    "TokenDiff",
]
