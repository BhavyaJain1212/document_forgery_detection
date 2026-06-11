"""Word-level bounding-box extraction via pdfplumber.

Used ONLY for the OVERLAY check (annotation rect overlapping a text region)
in diff/objectdiff.py. Never used for the text diff — see text.py for that.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import pdfplumber


@dataclass(frozen=True)
class WordBox:
    """A word with its page bounding box (pdfplumber coordinate system).

    ``top`` and ``bottom`` measure distance from the top-left of the page
    (y increases downward). ``x0`` / ``x1`` measure from the left edge,
    increasing rightward.
    """

    text: str
    x0: float
    top: float
    x1: float
    bottom: float


def extract_words_per_page(data: bytes) -> list[list[WordBox]]:
    """Return one list of WordBox per page, in page order (0-based index).

    On failure returns []. Used only by the OVERLAY classifier in objectdiff.
    """
    result: list[list[WordBox]] = []
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            for page in pdf.pages:
                words = page.extract_words() or []
                result.append([
                    WordBox(
                        text=w.get("text", ""),
                        x0=float(w.get("x0", 0.0)),
                        top=float(w.get("top", 0.0)),
                        x1=float(w.get("x1", 0.0)),
                        bottom=float(w.get("bottom", 0.0)),
                    )
                    for w in words
                ])
    except Exception:  # malformed / encrypted / corrupt: never crash
        pass
    return result
