"""Normalization + tolerance — the crux of separating OCR noise from divergence.

Folds away *recognised* OCR noise (NFC/case/space/confusion) on both sides, then
applies a length-scaled Levenshtein tolerance that is INVERTED for high-value
tokens (amount/date/ID get ZERO tolerance — a single-char delta IS the signal).
See ``docs/STAGE3_DESIGN.md`` §3. Runs on the CPU queue.

Token classification REUSES ``revision_recovery.highvalue.classify_token_kind``
— no second classifier.
"""

from __future__ import annotations

import re

from .config import OCRCrossCheckConfig
from .models import TokenClass

_CURRENCY_RE = re.compile(r"[₹$]|Rs\.?|INR", re.IGNORECASE)
_THOUSANDS_OR_DECIMAL_RE = re.compile(r"[.,]")


def fold(text: str, config: OCRCrossCheckConfig | None = None) -> str:
    """Fold one side of a comparison to its noise-canonical form (§3a).

    In order: NFC + zero-width/soft-hyphen stripping + whitespace collapse +
    trim (via the project normaliser), then casefold, then internal-space
    removal, then the OCR-confusion class fold (digraph substring passes first,
    then character substitutions).

    The fold is deliberately lossy: it erases exactly the deltas OCR is known
    to invent, so what survives is a real divergence.
    """
    cfg = config or OCRCrossCheckConfig()

    # Step 1 — project normaliser (NFC, strip ZW/soft-hyphen, collapse WS, trim)
    from ..revision_recovery.extract.normalize import normalize as _rr_normalize
    text = _rr_normalize(text)

    # Step 2 — casefold
    if cfg.fold_case:
        text = text.casefold()

    # Step 3 — drop internal spaces
    if cfg.fold_internal_spaces:
        text = text.replace(" ", "")

    # Step 4 — OCR-confusion fold.
    # The input is already casefolded, so we also casefold the class forms so that
    # e.g. ("8", "B") catches "b" (post-casefold of "B") — otherwise "B" → "b" by
    # casefold and the char_map entry for uppercase "B" would never fire.
    classes = cfg.ocr_confusion_classes
    if cfg.fold_case:
        classes = tuple(
            tuple(f.casefold() for f in cls)
            for cls in classes
        )
    text = _apply_confusion_fold(text, classes)

    return text


def _apply_confusion_fold(
    text: str,
    confusion_classes: tuple[tuple[str, ...], ...],
) -> str:
    """Apply the confusion-class fold to an already casefolded, space-stripped string.

    Two passes:
    1. Digraph substring replacements (multi-char forms like ``"rn"→"m"``).
    2. Character-level substitution (each non-canonical char → canonical).
    """
    char_map: dict[str, str] = {}
    digraphs: list[tuple[str, str]] = []  # (from_str, canonical)

    for cls in confusion_classes:
        if not cls:
            continue
        canonical = cls[0]
        for form in cls[1:]:
            if len(form) == 1:
                char_map[form] = canonical
            else:
                digraphs.append((form, canonical))

    # Digraphs first so character substitutions cannot partially disrupt them.
    for from_str, to_str in digraphs:
        text = text.replace(from_str, to_str)

    if char_map:
        text = "".join(char_map.get(c, c) for c in text)

    return text


def classify(token: str, *, context: str | None = None) -> TokenClass:
    """Map a token to its :class:`TokenClass` via the EXISTING classifier.

    Delegates to ``revision_recovery.highvalue.classify_token_kind`` and maps
    ``HighValueKind`` (AMOUNT/DATE/ID_LIKE) onto :class:`TokenClass`; everything
    else (including ``None``) is ``PROSE``.
    """
    from ..revision_recovery.highvalue import classify_token_kind
    from ..revision_recovery.models import HighValueKind

    kind = classify_token_kind(token)
    if kind is HighValueKind.AMOUNT:
        return TokenClass.AMOUNT
    if kind is HighValueKind.DATE:
        return TokenClass.DATE
    if kind is HighValueKind.ID_LIKE:
        return TokenClass.ID
    return TokenClass.PROSE


def is_monetary_amount(token: str) -> bool:
    """True when an AMOUNT-classified ``token`` carries real monetary context.

    A currency symbol/word (``₹ Rs INR $``), a decimal point, or a thousands
    separator makes the AMOUNT classification trustworthy. A bare 1-2 digit
    integer with none of these (e.g. a reservation term in years, a quantity,
    a list index) is NOT monetary — see
    ``OCRCrossCheckConfig.amount_requires_monetary_context``.
    """
    text = token.strip()
    if _CURRENCY_RE.search(text):
        return True
    if _THOUSANDS_OR_DECIMAL_RE.search(text):
        return True
    digits = re.sub(r"\D", "", text)
    if not digits:
        return False
    return len(digits) >= 3


def levenshtein(a: str, b: str) -> int:
    """Edit distance between two already-folded strings.

    Standard two-row DP, O(|a|·|b|) time, O(min(|a|,|b|)) space.
    """
    if a == b:
        return 0
    # Keep the shorter string as the column axis to minimise memory.
    if len(a) < len(b):
        a, b = b, a
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        curr = [i] + [0] * n
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j - 1], prev[j], curr[j - 1])
        prev = curr
    return prev[n]


def allowed_edits(
    token_class: TokenClass,
    length: int,
    config: OCRCrossCheckConfig | None = None,
) -> int:
    """Tolerance for a comparison, scaled to length and INVERTED for high-value.

    AMOUNT/DATE: ``0`` (strict — any surviving delta after confusion-fold is real).
    ID: ``0`` when ``id_strict``, else ``floor(length * id_rel_tol)`` (default —
    ID is the noisiest high-value class; it gets prose-grade tolerance).
    PROSE: ``max(prose_floor_edits, floor(length * prose_rel_tol))``.
    """
    cfg = config or OCRCrossCheckConfig()
    if token_class is TokenClass.AMOUNT:
        return cfg.amount_allowed_edits
    if token_class is TokenClass.DATE:
        return cfg.date_allowed_edits
    if token_class is TokenClass.ID:
        return 0 if cfg.id_strict else int(length * cfg.id_rel_tol)
    # PROSE
    return max(cfg.prose_floor_edits, int(length * cfg.prose_rel_tol))


def is_within_tolerance(
    embedded: str,
    ocr: str,
    token_class: TokenClass,
    config: OCRCrossCheckConfig | None = None,
) -> bool:
    """True when folded ``embedded``/``ocr`` agree within the class tolerance.

    Fold both sides (§3a), compute Levenshtein, compare against ``allowed_edits``
    for the token class.
    """
    cfg = config or OCRCrossCheckConfig()
    fe = fold(embedded, cfg)
    fo = fold(ocr, cfg)
    d = levenshtein(fe, fo)
    max_len = max(len(fe), len(fo), 1)  # guard against both-empty edge case
    tol = allowed_edits(token_class, max_len, cfg)
    return d <= tol


__all__ = [
    "fold",
    "classify",
    "is_monetary_amount",
    "levenshtein",
    "allowed_edits",
    "is_within_tolerance",
]
