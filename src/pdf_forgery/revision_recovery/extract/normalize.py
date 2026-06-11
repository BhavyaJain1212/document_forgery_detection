"""Shared text normalisation for revision-recovery text and object diffs.

``normalize()`` and ``tokenize()`` are called identically by ``textdiff`` and
``objectdiff`` so the two diffs can never drift apart on normalisation.

When a :class:`~pdf_forgery.revision_recovery.config.Config` is supplied its
normalisation toggles are applied; when it is ``None`` all normalisations are
enabled (spec defaults).
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Config

# U+200B ZERO WIDTH SPACE, U+200C ZW NON-JOINER, U+200D ZW JOINER, U+00AD SOFT HYPHEN
_ZW_SOFT_HYPHEN = re.compile("[​‌‍­]")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize(text: str, config: "Config | None" = None) -> str:
    """NFC → strip zero-width + soft-hyphen → collapse whitespace → trim.

    When *config* is ``None`` all operations are applied (spec defaults).
    """
    nfc = config.nfc_normalize if config is not None else True
    strip_zw = config.strip_zero_width if config is not None else True
    collapse_ws = config.collapse_whitespace if config is not None else True

    if nfc:
        text = unicodedata.normalize("NFC", text)
    if strip_zw:
        text = _ZW_SOFT_HYPHEN.sub("", text)
    if collapse_ws:
        text = _WHITESPACE_RUN.sub(" ", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    """Split normalised text on whitespace; returns ``[]`` for empty input."""
    return text.split()
