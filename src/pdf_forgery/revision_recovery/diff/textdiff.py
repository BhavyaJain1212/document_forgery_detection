"""Token-level text diff between consecutive reconstructed revisions.

Pipeline:
  extract_text_per_page -> normalize -> tokenize -> SequenceMatcher (tokens)
  -> for each changed token: SequenceMatcher (chars) + high-value classification

Public entry points
-------------------
diff_text(rev_a, rev_b)
    Full pipeline from two Revision objects.

diff_normalized_pages(pages_a, pages_b, from_revision, to_revision)
    Accepts already-normalized page-text lists; exposed for independent testing.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from itertools import zip_longest

from ..extract.normalize import normalize, tokenize
from ..extract.text import extract_text_per_page
from ..highvalue import classify_change, classify_token
from ..models import (
    CharSpan,
    HighValueKind,
    PageTextDiff,
    Revision,
    TextChange,
    TokenDiff,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _char_diff(before: str, after: str) -> tuple[CharSpan, ...]:
    """Character-level diff between two strings using difflib opcodes."""
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    return tuple(
        CharSpan(tag=tag, before=before[i1:i2], after=after[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    )


def _diff_page(
    page_index: int,
    text_a: str,
    text_b: str,
) -> PageTextDiff:
    """Compute the token-level diff for one page pair."""
    tokens_a = tokenize(text_a)
    tokens_b = tokenize(text_b)

    if tokens_a == tokens_b:
        return PageTextDiff(
            page_index=page_index,
            before_text=text_a,
            after_text=text_b,
            token_changes=(),
            is_substantive=False,
            has_high_value_change=False,
        )

    token_changes: list[TokenDiff] = []

    matcher = SequenceMatcher(None, tokens_a, tokens_b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        if tag == "replace":
            # Pair up old and new tokens; extras on either side become pure
            # inserts/deletes within the replace block.
            for old, new in zip_longest(
                tokens_a[i1:i2], tokens_b[j1:j2], fillvalue=""
            ):
                hv = classify_change(old, new)
                token_changes.append(
                    TokenDiff(
                        before=old,
                        after=new,
                        char_diff=_char_diff(old, new),
                        high_value=hv,
                    )
                )

        elif tag == "delete":
            for old in tokens_a[i1:i2]:
                token_changes.append(
                    TokenDiff(
                        before=old,
                        after="",
                        char_diff=_char_diff(old, ""),
                        high_value=classify_token(old),
                    )
                )

        elif tag == "insert":
            for new in tokens_b[j1:j2]:
                token_changes.append(
                    TokenDiff(
                        before="",
                        after=new,
                        char_diff=_char_diff("", new),
                        high_value=classify_token(new),
                    )
                )

    has_hv = any(tc.high_value is not None for tc in token_changes)
    return PageTextDiff(
        page_index=page_index,
        before_text=text_a,
        after_text=text_b,
        token_changes=tuple(token_changes),
        is_substantive=bool(token_changes),
        has_high_value_change=has_hv,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diff_normalized_pages(
    pages_a: list[str],
    pages_b: list[str],
    from_revision: int = 0,
    to_revision: int = 1,
) -> TextChange:
    """Diff pre-normalized page-text lists and return a :class:`TextChange`.

    Aligns pages by index (``zip_longest`` with empty string for missing pages).
    Exposed as a stable, independently testable entry point — tests can pass
    hand-crafted strings without needing real PDF fixtures.
    """
    notes: list[str] = []
    if len(pages_a) != len(pages_b):
        notes.append(
            f"page count changed: revision {from_revision} has {len(pages_a)} "
            f"page(s), revision {to_revision} has {len(pages_b)} page(s)"
        )

    page_diffs = [
        _diff_page(idx, text_a, text_b)
        for idx, (text_a, text_b) in enumerate(
            zip_longest(pages_a, pages_b, fillvalue="")
        )
    ]

    is_sub = any(pd.is_substantive for pd in page_diffs)
    has_hv = any(pd.has_high_value_change for pd in page_diffs)

    return TextChange(
        from_revision=from_revision,
        to_revision=to_revision,
        page_diffs=tuple(page_diffs),
        is_substantive=is_sub,
        has_high_value_change=has_hv,
        notes=tuple(notes),
    )


def diff_text(rev_a: Revision, rev_b: Revision) -> TextChange:
    """Extract, normalize, and diff the text layers of two :class:`Revision` objects.

    Extraction failures (empty list from a revision that should have pages) are
    recorded in ``notes``; the diff is computed on whatever pages were extracted.
    """
    raw_a = extract_text_per_page(rev_a.data)
    raw_b = extract_text_per_page(rev_b.data)

    pages_a = [normalize(p) for p in raw_a]
    pages_b = [normalize(p) for p in raw_b]

    notes: list[str] = []
    if rev_a.page_count > 0 and not raw_a:
        notes.append(
            f"text extraction failed for revision {rev_a.index} "
            f"(page_count={rev_a.page_count})"
        )
    if rev_b.page_count > 0 and not raw_b:
        notes.append(
            f"text extraction failed for revision {rev_b.index} "
            f"(page_count={rev_b.page_count})"
        )

    result = diff_normalized_pages(pages_a, pages_b, rev_a.index, rev_b.index)

    if notes:
        return TextChange(
            from_revision=result.from_revision,
            to_revision=result.to_revision,
            page_diffs=result.page_diffs,
            is_substantive=result.is_substantive,
            has_high_value_change=result.has_high_value_change,
            notes=result.notes + tuple(notes),
        )
    return result
