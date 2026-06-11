"""Shared text normalization for revision-recovery text and object diffs.

normalize() + tokenize() are called identically by textdiff and objectdiff so
the two diffs cannot drift apart on normalization.
"""

from __future__ import annotations

import re
import unicodedata

# U+200B ZERO WIDTH SPACE, U+200C ZW NON-JOINER, U+200D ZW JOINER, U+00AD SOFT HYPHEN
_ZW_SOFT_HYPHEN = re.compile("[​‌‍­]")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize(text: str) -> str:
    """NFC -> strip zero-width + soft-hyphen -> collapse whitespace -> trim."""
    text = unicodedata.normalize("NFC", text)
    text = _ZW_SOFT_HYPHEN.sub("", text)
    text = _WHITESPACE_RUN.sub(" ", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    """Split normalized text on whitespace; returns [] for empty/whitespace-only input."""
    return text.split()
