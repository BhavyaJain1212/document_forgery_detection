"""High-value token pattern matchers: amount/date (strong), ID-like (weak).

Classify individual tokens against the rubric patterns. All compiled regexes
are module-level constants here; Task 5 (config.py) will expose them as
configurable overrides.
"""

from __future__ import annotations

import re

from .models import HighValueKind

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Amount / currency (strong)
# Matches:
#   - Optionally prefixed by ₹, $, Rs, or INR
#   - Then either a comma-grouped number (Indian/Western: 5,000 / 1,20,000.00)
#     or a plain integer or decimal (50000 / 100.50)
#   - Also matches standalone currency symbols/abbreviations used as separate tokens
_AMOUNT_RE = re.compile(
    r"^(?:[₹$]|Rs|INR)?"
    r"(?:\d{1,3}(?:,\d{2,3})+(?:\.\d+)?"   # comma-grouped: 5,000 / 1,20,000.00
    r"|\d+(?:\.\d+)?)"                       # plain: 50000 / 100.50
    r"$"
    r"|^(?:[₹$]|Rs|INR)$",                  # standalone currency marker
)

# Date (strong) — single-token numeric formats only.
# Month-name dates ("15 January 2024") span multiple whitespace-split tokens
# and require n-gram context; they are not handled here.
_DATE_RE = re.compile(
    r"^(?:"
    r"\d{1,2}[/\-]\d{1,2}[/\-]\d{4}"   # dd/mm/yyyy  or  dd-mm-yyyy
    r"|\d{4}-\d{2}-\d{2}"               # ISO 8601: yyyy-mm-dd
    r")$"
)

# ID-like (weak — noisy)
# Any token that contains a run of >= 6 consecutive alphanumeric characters.
# Catches policy/claim numbers (e.g. POL12345, CLM-2024-001) but also common
# long English words; treat as a weak confidence booster.
_ID_LIKE_RE = re.compile(r"[A-Za-z0-9]{6,}")

# Priority order (lower int = higher priority) used to resolve ties.
_KIND_PRIORITY: dict[HighValueKind, int] = {
    HighValueKind.AMOUNT: 0,
    HighValueKind.DATE: 1,
    HighValueKind.ID_LIKE: 2,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_token(token: str) -> HighValueKind | None:
    """Return the highest-priority :class:`HighValueKind` for *token*, or ``None``.

    Checks AMOUNT first (strongest), then DATE, then ID_LIKE (weakest).
    Returns on the first match so higher-priority categories always win.
    """
    if _AMOUNT_RE.fullmatch(token):
        return HighValueKind.AMOUNT
    if _DATE_RE.fullmatch(token):
        return HighValueKind.DATE
    if _ID_LIKE_RE.search(token):
        return HighValueKind.ID_LIKE
    return None


def classify_change(before: str, after: str) -> HighValueKind | None:
    """Return the highest-priority kind found in *either* side of a changed token.

    Used for ``replace`` operations where we check both the removed and added
    token and surface the most significant match.
    """
    kinds = [k for k in (classify_token(before), classify_token(after)) if k is not None]
    if not kinds:
        return None
    return min(kinds, key=lambda k: _KIND_PRIORITY[k])
