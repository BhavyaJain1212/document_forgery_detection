"""Diff modules for Stage 1 (revision recovery).

- textdiff: token-level text diff per page + char-level detail on changed tokens
- objectdiff: overridden PDF object detection + classification (Task 4b)
"""

from .textdiff import diff_normalized_pages, diff_text

__all__ = ["diff_text", "diff_normalized_pages"]
