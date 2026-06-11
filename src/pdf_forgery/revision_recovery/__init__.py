"""Stage 1 — revision recovery.

Detects direct-text-editing forgery by recovering the historical revisions an
incremental update leaves inside a PDF and proving what text changed.

Implemented so far:
    - detect: enumerate candidate revision boundaries from raw bytes.

Proposed / not yet implemented (see ../../CLAUDE.md): reconstruct, extract,
diff, highvalue, scoring, report.
"""

from .detect import detect, detect_from_path
from .models import DetectionResult, EOFMarker, RevisionBoundary, XrefSection

__all__ = [
    "detect",
    "detect_from_path",
    "DetectionResult",
    "EOFMarker",
    "RevisionBoundary",
    "XrefSection",
]
