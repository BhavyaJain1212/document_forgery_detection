"""Pure helpers for reasoning about PDF font names.

pdfminer reports a per-character ``fontname`` string. For embedded subset fonts
that string carries a 6-uppercase-letter subset prefix and a ``+`` separator,
e.g. ``ABCDEF+Arial``; for the standard 14 fonts it is just the base name, e.g.
``Helvetica`` or ``Times-Roman``. Style variants append a suffix, e.g.
``Helvetica-Bold`` or ``Arial,BoldItalic``.

The font-forensics detectors compare these names at three granularities:

    raw fontname  — exact identity (``ABCDEF+Arial``)
    base face     — the name with any subset tag stripped (``Arial``)
    family root   — the base face with any style suffix stripped (``Arial``)

A *same-base / different-subset-tag* split is the re-embedding fingerprint the
detector treats as strong evidence; a mere *family-root* match across two base
faces (``Helvetica`` vs ``Helvetica-Bold``) is ordinary emphasis and must NOT be
treated as suspicious — that distinction is what keeps false positives down on
genuine multi-font documents.

All functions here are pure and side-effect free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A subset tag is exactly six uppercase ASCII letters followed by '+'.
_SUBSET_TAG_RE = re.compile(r"^([A-Z]{6})\+(.+)$")

# Style suffixes appended to a base face, after splitting on '-' or ','.
# Matched case-insensitively against each trailing component.
_STYLE_WORDS = frozenset(
    {
        "bold",
        "italic",
        "oblique",
        "bolditalic",
        "boldoblique",
        "regular",
        "roman",
        "medium",
        "light",
        "semibold",
        "demibold",
        "black",
        "heavy",
        "thin",
        "condensed",
        "narrow",
        "mt",
        "ps",
        "psmt",
    }
)

# Foundry/format markers appended to a face name; never family-distinguishing.
# Longest-first so a name ending in ``PSMT`` does not stop at ``MT``.
_VENDOR_SUFFIXES = ("PSMT", "PS", "MT")

# Empty / missing font names pdfminer may emit.
_PLACEHOLDER_NAMES = frozenset({"", "unknown"})


@dataclass(frozen=True)
class FontIdentity:
    """A parsed pdfminer font name decomposed for comparison.

    ``raw`` is the original string; ``subset_tag`` is the 6-letter prefix (or
    ``None`` when the font is not a subset); ``base`` is the name with the tag
    removed; ``family`` is the base with any style suffix removed.
    """

    raw: str
    subset_tag: str | None
    base: str
    family: str

    @property
    def is_subset(self) -> bool:
        """True when the font name carried a 6-letter subset prefix."""
        return self.subset_tag is not None

    @property
    def is_placeholder(self) -> bool:
        """True when pdfminer could not name the font (``unknown`` / empty)."""
        return self.base.lower() in _PLACEHOLDER_NAMES


def parse_font_identity(fontname: str | None) -> FontIdentity:
    """Decompose a pdfminer ``fontname`` into subset tag / base / family."""
    raw = fontname or ""
    m = _SUBSET_TAG_RE.match(raw)
    if m:
        tag: str | None = m.group(1)
        base = m.group(2)
    else:
        tag = None
        base = raw
    return FontIdentity(raw=raw, subset_tag=tag, base=base, family=_family_root(base))


def _family_root(base: str) -> str:
    """Strip vendor markers and trailing style words from a base face."""
    # Split on the common separators PDF font names use between family and style.
    parts = re.split(r"[-,]", base)
    root = [_strip_vendor_suffix(parts[0])]
    for part in parts[1:]:
        if _strip_vendor_suffix(part).lower() in _STYLE_WORDS:
            continue
        root.append(part)
    return "-".join(root)


def _strip_vendor_suffix(part: str) -> str:
    """Remove one trailing foundry/format marker, preserving marker-only parts."""
    for suffix in _VENDOR_SUFFIXES:
        if len(part) > len(suffix) and part.upper().endswith(suffix):
            return part[: -len(suffix)]
    return part


def same_base_different_subset(a: FontIdentity, b: FontIdentity) -> bool:
    """True when two fonts share a base face but carry different subset tags.

    This is the re-embedding fingerprint: the same face appears twice with two
    independently-generated subset prefixes (``ABCDEF+Arial`` vs ``GHIJKL+Arial``),
    which a single clean PDF producer essentially never emits.
    """
    if a.base != b.base:
        return False
    if a.subset_tag is None or b.subset_tag is None:
        return False
    return a.subset_tag != b.subset_tag


def is_style_variant(a: FontIdentity, b: FontIdentity) -> bool:
    """True when two different base faces are just style variants of one family.

    ``Helvetica`` vs ``Helvetica-Bold`` (same family root, differing only by an
    emphasis suffix) is ordinary styling, not a substitution. Identical base
    faces also count as style variants (nothing changed).
    """
    if a.base == b.base:
        return True
    return a.family == b.family and a.family != ""


def is_substitution(a: FontIdentity, b: FontIdentity) -> bool:
    """True when two fonts are different *families* (a genuine substitution).

    Excludes same-base, style-variant, and placeholder comparisons. A True here
    means the two glyphs were set in unrelated typefaces.
    """
    if a.is_placeholder or b.is_placeholder:
        return False
    if a.base == b.base:
        return False
    if is_style_variant(a, b):
        return False
    return True
