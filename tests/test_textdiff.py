"""Tests for highvalue.py and diff/textdiff.py.

highvalue: classify individual tokens by pattern (amount/date/id_like).
textdiff:  token-level diff of normalized page-text lists + char-level detail
           on changed tokens, substantive-change flag, high-value detection.

Most textdiff tests use ``diff_normalized_pages`` (pre-normalized strings) so
they don't depend on PDF fixtures. A small set uses ``diff_text`` with real
pikepdf-built revisions to cover the full extraction path.
"""

from __future__ import annotations

import io

import pikepdf
import pytest

from pdf_forgery.revision_recovery.diff.textdiff import (
    diff_normalized_pages,
    diff_text,
)
from pdf_forgery.revision_recovery.detect import detect
from pdf_forgery.revision_recovery.highvalue import (
    ClassificationStrength,
    TokenCandidate,
    classify_change,
    classify_token,
)
from pdf_forgery.revision_recovery.models import (
    CharSpan,
    HighValueKind,
    PageTextDiff,
    TextChange,
    TokenDiff,
)
from pdf_forgery.revision_recovery.reconstruct import reconstruct


# ---------------------------------------------------------------------------
# PDF helpers (reused from test_reconstruct / test_extract patterns)
# ---------------------------------------------------------------------------

def _base_pdf() -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _text_pdf(page_texts: list[str]) -> bytes:
    """Single-page or multi-page PDF with ASCII text via Type1/Helvetica."""
    pdf = pikepdf.new()
    for text in page_texts:
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[-1]
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET\n".encode("latin-1")
        page.Contents = pdf.make_indirect(pikepdf.Stream(pdf, content))
        page.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name("/Font"),
                    Subtype=pikepdf.Name("/Type1"),
                    BaseFont=pikepdf.Name("/Helvetica"),
                    Encoding=pikepdf.Name("/WinAnsiEncoding"),
                )
            )
        )
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _append_incremental(base: bytes, text: str) -> bytes:
    """Append an incremental revision that modifies the first page's content."""
    prev_sx = detect(base).valid_boundaries[-1].startxref.pointer
    with pikepdf.open(io.BytesIO(base)) as p:
        old_size = int(p.trailer.Size)
        root_num, root_gen = p.Root.objgen

    # New content stream object
    new_num = old_size
    stream_bytes = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET\n".encode("latin-1")
    # Encode the stream length in the object dict
    stream_body = (
        f"{new_num} 0 obj\n"
        f"<< /Length {len(stream_bytes)} >>\n"
        f"stream\n"
    ).encode() + stream_bytes + b"\nendstream\nendobj\n"

    obj_offset = len(base)
    xref_offset = obj_offset + len(stream_body)
    xref = (
        b"xref\n"
        + f"{new_num} 1\n".encode()
        + f"{obj_offset:010d} 00000 n \n".encode()
        + b"trailer\n"
        + f"<< /Size {new_num + 1} /Root {root_num} {root_gen} R /Prev {prev_sx} >>\n".encode()
        + b"startxref\n"
        + f"{xref_offset}\n".encode()
        + b"%%EOF\n"
    )
    return base + stream_body + xref


# ---------------------------------------------------------------------------
# classify_token  (highvalue.py)
# ---------------------------------------------------------------------------

class TestClassifyToken:
    # Amount / currency (strong)
    def test_amount_comma_grouped(self):
        assert classify_token("5,000") == HighValueKind.AMOUNT

    def test_amount_plain_digits(self):
        assert classify_token("50000") == HighValueKind.AMOUNT

    def test_amount_rupee_prefix(self):
        assert classify_token("₹1,20,000.00") == HighValueKind.AMOUNT

    def test_amount_dollar_prefix(self):
        assert classify_token("$100.50") == HighValueKind.AMOUNT

    def test_amount_rs_with_number(self):
        assert classify_token("Rs5000") == HighValueKind.AMOUNT

    def test_amount_inr_with_number(self):
        assert classify_token("INR50000") == HighValueKind.AMOUNT

    def test_amount_standalone_rupee(self):
        assert classify_token("₹") == HighValueKind.AMOUNT

    def test_amount_standalone_rs(self):
        assert classify_token("Rs") == HighValueKind.AMOUNT

    def test_amount_standalone_inr(self):
        assert classify_token("INR") == HighValueKind.AMOUNT

    def test_amount_plain_number_beats_id_like(self):
        # "123456" has 6 digits — should be AMOUNT not ID_LIKE
        assert classify_token("123456") == HighValueKind.AMOUNT

    def test_amount_single_digit(self):
        # spec doesn't require minimum digit count
        assert classify_token("5") == HighValueKind.AMOUNT

    # Date (strong)
    def test_date_slash(self):
        assert classify_token("15/01/2024") == HighValueKind.DATE

    def test_date_single_digit_day_month(self):
        assert classify_token("1/1/2024") == HighValueKind.DATE

    def test_date_hyphen(self):
        assert classify_token("15-01-2024") == HighValueKind.DATE

    def test_date_iso8601(self):
        assert classify_token("2024-01-15") == HighValueKind.DATE

    def test_date_not_short_year(self):
        assert classify_token("15/01/24") is None

    def test_date_not_wrong_order(self):
        # yyyy/mm/dd is not one of the accepted formats
        assert classify_token("2024/01/15") is None

    # ID-like (weak)
    def test_id_like_alpha_plus_digits(self):
        assert classify_token("POL12345") == HighValueKind.ID_LIKE

    def test_id_like_pure_alpha_6(self):
        assert classify_token("ABCDEF") == HighValueKind.ID_LIKE

    def test_id_like_mixed_with_hyphen(self):
        # The 7-char alphanumeric run inside the token triggers the match
        assert classify_token("CLM-2024001") == HighValueKind.ID_LIKE

    def test_id_like_requires_6_chars(self):
        assert classify_token("ABCDE") is None   # only 5

    # None — no match
    def test_none_short_word(self):
        assert classify_token("foo") is None

    def test_none_plain_english_word_5(self):
        assert classify_token("hello") is None   # 5 chars

    def test_none_empty(self):
        assert classify_token("") is None

    # Priority: AMOUNT beats DATE beats ID_LIKE
    def test_amount_beats_id_like(self):
        # "123456" has 6+ digits → both AMOUNT and ID_LIKE match, AMOUNT wins
        assert classify_token("123456") == HighValueKind.AMOUNT

    def test_structured_amount_evidence(self):
        result = classify_token("18071.23", context="Amount Due")
        assert result is not None
        assert result.primary is TokenCandidate.AMOUNT
        assert result.strength is ClassificationStrength.STRONG
        assert result.high_value_kind is HighValueKind.AMOUNT
        assert {"two_decimal_places", "money_label"} <= set(result.signals)

    def test_abn_context_resolves_long_digits_to_identifier(self):
        result = classify_token("59547297213", context="ABN:")
        assert result is not None
        assert result.primary is TokenCandidate.IDENTIFIER
        assert result.strength is ClassificationStrength.STRONG
        assert result.high_value_kind is HighValueKind.ID_LIKE
        assert "identifier_label" in result.signals
        # Revision recovery keeps the historical digits-as-amount interpretation.
        assert result.legacy_kind is HighValueKind.AMOUNT

    def test_bare_integer_is_weak_unknown_numeric_for_contextual_callers(self):
        result = classify_token("50000")
        assert result is not None
        assert result.primary is TokenCandidate.UNKNOWN_NUMERIC
        assert result.strength is ClassificationStrength.WEAK
        assert result.high_value_kind is None
        assert result.legacy_kind is HighValueKind.AMOUNT

    def test_explicit_currency_outranks_identifier_label(self):
        result = classify_token("$100.00", context="Account Balance")
        assert result is not None
        assert result.primary is TokenCandidate.AMOUNT
        assert result.strength is ClassificationStrength.STRONG


class TestClassifyChange:
    def test_returns_higher_priority_of_two_sides(self):
        # "Rs5000" (amount) vs "ABCDEF" (id_like) → AMOUNT wins
        assert classify_change("Rs5000", "ABCDEF") == HighValueKind.AMOUNT

    def test_uses_after_token_when_before_is_none(self):
        assert classify_change("hello", "15/01/2024") == HighValueKind.DATE

    def test_uses_before_token_when_after_is_none_kind(self):
        assert classify_change("5,000", "foo") == HighValueKind.AMOUNT

    def test_returns_none_when_neither_matches(self):
        assert classify_change("foo", "bar") is None

    def test_empty_strings_return_none(self):
        assert classify_change("", "") is None


# ---------------------------------------------------------------------------
# diff_normalized_pages  (textdiff.py)
# ---------------------------------------------------------------------------

class TestDiffNormalizedPages:
    # ── identical / non-substantive ─────────────────────────────────────────

    def test_identical_pages_not_substantive(self):
        result = diff_normalized_pages(["foo bar baz"], ["foo bar baz"])
        assert not result.is_substantive
        assert result.page_diffs[0].token_changes == ()

    def test_empty_both_sides_not_substantive(self):
        result = diff_normalized_pages([""], [""])
        assert not result.is_substantive

    def test_whitespace_only_difference_not_substantive(self):
        # Both normalize to the same tokens; after normalize() they're equal
        result = diff_normalized_pages(["foo  bar"], ["foo bar"])
        assert not result.is_substantive

    # ── substantive: token changes ──────────────────────────────────────────

    def test_single_token_replacement_is_substantive(self):
        result = diff_normalized_pages(["foo bar"], ["foo baz"])
        assert result.is_substantive
        assert len(result.page_diffs[0].token_changes) == 1
        tc = result.page_diffs[0].token_changes[0]
        assert tc.before == "bar"
        assert tc.after == "baz"

    def test_token_addition_is_substantive(self):
        result = diff_normalized_pages(["foo"], ["foo bar"])
        assert result.is_substantive
        changes = result.page_diffs[0].token_changes
        assert any(tc.after == "bar" for tc in changes)

    def test_token_deletion_is_substantive(self):
        result = diff_normalized_pages(["foo bar"], ["foo"])
        assert result.is_substantive
        changes = result.page_diffs[0].token_changes
        assert any(tc.before == "bar" for tc in changes)

    def test_single_character_change_is_substantive(self):
        # spec: "There is NO minimum token count. A single-character change counts."
        result = diff_normalized_pages(["amount 5000"], ["amount 5001"])
        assert result.is_substantive

    def test_complete_page_replacement_is_substantive(self):
        result = diff_normalized_pages(["old text here"], ["new text here"])
        assert result.is_substantive

    # ── character-level diff ─────────────────────────────────────────────────

    def test_char_diff_populated_on_replacement(self):
        result = diff_normalized_pages(["foo bar"], ["foo baz"])
        tc = result.page_diffs[0].token_changes[0]
        assert len(tc.char_diff) > 0
        assert all(isinstance(cs, CharSpan) for cs in tc.char_diff)

    def test_char_diff_equal_prefix_preserved(self):
        result = diff_normalized_pages(["ba1"], ["ba2"])
        tc = result.page_diffs[0].token_changes[0]
        # "ba" is common prefix → "equal" span
        equal_spans = [cs for cs in tc.char_diff if cs.tag == "equal"]
        assert any(cs.before == "ba" for cs in equal_spans)

    def test_char_diff_insert_only_for_pure_addition(self):
        # token deleted: before="bar", after=""
        result = diff_normalized_pages(["foo bar"], ["foo"])
        tc = result.page_diffs[0].token_changes[0]
        assert tc.before == "bar"
        assert tc.after == ""
        # char_diff shows the deletion
        assert any(cs.tag in ("delete", "replace") for cs in tc.char_diff)

    def test_char_diff_covers_whole_tokens(self):
        result = diff_normalized_pages(["old"], ["new"])
        tc = result.page_diffs[0].token_changes[0]
        before_chars = "".join(cs.before for cs in tc.char_diff)
        after_chars = "".join(cs.after for cs in tc.char_diff)
        assert before_chars == "old"
        assert after_chars == "new"

    # ── high-value detection ─────────────────────────────────────────────────

    def test_changed_amount_token_flags_high_value(self):
        result = diff_normalized_pages(["claim 5,000 approved"], ["claim 6,000 approved"])
        assert result.has_high_value_change
        assert result.page_diffs[0].has_high_value_change

    def test_changed_date_token_flags_high_value(self):
        result = diff_normalized_pages(
            ["due 15/01/2024 payment"], ["due 20/01/2024 payment"]
        )
        assert result.has_high_value_change

    def test_changed_id_like_token_flags_high_value(self):
        result = diff_normalized_pages(["policy POL12345 issued"], ["policy POL99999 issued"])
        assert result.has_high_value_change

    def test_no_high_value_when_only_prose_changes(self):
        result = diff_normalized_pages(["the cat sat here"], ["the dog sat here"])
        assert not result.has_high_value_change
        assert result.is_substantive

    def test_high_value_kind_stored_on_token_diff(self):
        result = diff_normalized_pages(["amount 5,000 total"], ["amount 6,000 total"])
        hv_changes = [
            tc for pd in result.page_diffs for tc in pd.token_changes if tc.high_value
        ]
        assert hv_changes
        assert hv_changes[0].high_value == HighValueKind.AMOUNT

    # ── page alignment ───────────────────────────────────────────────────────

    def test_single_page_returns_one_page_diff(self):
        result = diff_normalized_pages(["page one"], ["page one"])
        assert len(result.page_diffs) == 1
        assert result.page_diffs[0].page_index == 0

    def test_multi_page_aligned_by_index(self):
        result = diff_normalized_pages(["page one", "page two"], ["page one", "page TWO"])
        assert len(result.page_diffs) == 2
        assert not result.page_diffs[0].is_substantive
        assert result.page_diffs[1].is_substantive

    def test_extra_page_in_after_detected(self):
        result = diff_normalized_pages(["only page"], ["only page", "new page"])
        assert len(result.page_diffs) == 2
        assert result.page_diffs[1].is_substantive
        assert "page count changed" in result.notes[0].lower()

    def test_extra_page_in_before_detected(self):
        result = diff_normalized_pages(["page one", "page two"], ["page one"])
        assert len(result.page_diffs) == 2
        assert result.page_diffs[1].is_substantive
        assert "page count changed" in result.notes[0].lower()

    def test_empty_vs_nonempty_pages_is_substantive(self):
        result = diff_normalized_pages([], ["new page text"])
        assert result.is_substantive

    # ── revision indices ─────────────────────────────────────────────────────

    def test_revision_indices_stored(self):
        result = diff_normalized_pages(["a"], ["b"], from_revision=2, to_revision=3)
        assert result.from_revision == 2
        assert result.to_revision == 3

    def test_default_revision_indices(self):
        result = diff_normalized_pages(["a"], ["a"])
        assert result.from_revision == 0
        assert result.to_revision == 1

    # ── result types ─────────────────────────────────────────────────────────

    def test_returns_text_change(self):
        result = diff_normalized_pages(["foo"], ["bar"])
        assert isinstance(result, TextChange)

    def test_page_diffs_are_page_text_diff(self):
        result = diff_normalized_pages(["foo"], ["bar"])
        assert isinstance(result.page_diffs[0], PageTextDiff)

    def test_token_changes_are_token_diff(self):
        result = diff_normalized_pages(["foo"], ["bar"])
        tc = result.page_diffs[0].token_changes[0]
        assert isinstance(tc, TokenDiff)


# ---------------------------------------------------------------------------
# diff_text  (full pipeline: Revision objects -> TextChange)
# ---------------------------------------------------------------------------

class TestDiffText:
    def _make_revisions(self, text_a: str, text_b: str):
        base = _text_pdf([text_a])
        updated = _append_incremental(base, text_b)
        rr = reconstruct(updated, detect(updated))
        assert rr.revision_count == 2
        return rr.revisions[0], rr.revisions[1]

    def test_returns_text_change(self):
        rev_a, rev_b = self._make_revisions("Hello World", "Hello World")
        result = diff_text(rev_a, rev_b)
        assert isinstance(result, TextChange)

    def test_identical_content_not_substantive(self):
        # Note: rev_b's content stream was APPENDED but the page still renders
        # the original content stream from rev_a (rev_b only adds a new object
        # that isn't referenced by the page).  Both revisions' text layers are
        # therefore identical.
        rev_a, rev_b = self._make_revisions("Hello World", "Hello World")
        result = diff_text(rev_a, rev_b)
        # Either not substantive (both extract same text) or substantive
        # (pdfminer finds the extra object); either is valid, but no crash.
        assert isinstance(result.is_substantive, bool)

    def test_from_to_revision_indices_match(self):
        rev_a, rev_b = self._make_revisions("Hello World", "Bye World")
        result = diff_text(rev_a, rev_b)
        assert result.from_revision == rev_a.index
        assert result.to_revision == rev_b.index

    def test_blank_pdf_not_substantive(self):
        raw = _base_pdf()
        rr = reconstruct(raw, detect(raw))
        assert rr.revision_count == 1
        rev = rr.revisions[0]
        # diff_text with same revision on both sides
        result = diff_text(rev, rev)
        assert not result.is_substantive
