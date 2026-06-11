"""Stage 1 — revision recovery.

Detects direct-text-editing forgery by recovering the historical revisions an
incremental update leaves inside a PDF and proving what text changed.

Implemented so far:
    - detect: enumerate candidate revision boundaries from raw bytes.
    - reconstruct: truncate + load-validate each valid boundary with pikepdf.

Proposed / not yet implemented (see ../../CLAUDE.md): extract, diff, highvalue,
scoring, report.
"""

from .detect import detect, detect_from_path
from .models import (
    DetectionResult,
    EOFMarker,
    ReconstructionFailure,
    ReconstructionResult,
    Revision,
    RevisionBoundary,
    XrefSection,
)
from .reconstruct import reconstruct, reconstruct_from_path

__all__ = [
    "detect",
    "detect_from_path",
    "reconstruct",
    "reconstruct_from_path",
    "DetectionResult",
    "EOFMarker",
    "RevisionBoundary",
    "XrefSection",
    "Revision",
    "ReconstructionFailure",
    "ReconstructionResult",
]
