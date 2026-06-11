"""Diff modules for Stage 1 (revision recovery).

- textdiff: token-level text diff per page + char-level detail on changed tokens
- objectdiff: overridden PDF object detection + classification
"""

from .objectdiff import classify_changed_object, diff_objects
from .textdiff import diff_normalized_pages, diff_text

__all__ = [
    "classify_changed_object",
    "diff_objects",
    "diff_text",
    "diff_normalized_pages",
]
