"""Per-page text-layer extraction via pdfminer.six.

Deterministic, fully local; no network or cloud calls. Accepts PDF bytes
(a Revision's .data field) and returns one raw text string per page.
Normalization is a separate step (see normalize.py).
"""

from __future__ import annotations

from io import BytesIO

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer


def extract_text_per_page(data: bytes) -> list[str]:
    """Return one raw text string per page, in page order (0-based index).

    On total failure (malformed, encrypted-without-password, empty) returns [].
    On per-page error the page entry is an empty string and iteration continues.
    """
    pages: list[str] = []
    try:
        for page_layout in extract_pages(BytesIO(data)):
            texts: list[str] = []
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    texts.append(element.get_text())
            pages.append("".join(texts))
    except Exception:  # malformed / encrypted / corrupt: never crash
        pass
    return pages
