"""Tests for extract/normalize.py, extract/text.py, and extract/words.py.

normalize and tokenize are pure string functions — no PDFs needed.
text and words extraction need real loadable PDFs; fixtures are built with
pikepdf so we control content precisely.
"""

from __future__ import annotations

import io
import unicodedata

import pikepdf
import pytest

from pdf_forgery.revision_recovery.extract.normalize import normalize, tokenize
from pdf_forgery.revision_recovery.extract.text import extract_text_per_page
from pdf_forgery.revision_recovery.extract.words import WordBox, extract_words_per_page


# --------------------------------------------------------------------------- #
# PDF fixtures
# --------------------------------------------------------------------------- #

def _blank_pdf(page_count: int = 1) -> bytes:
    pdf = pikepdf.new()
    for _ in range(page_count):
        pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _text_pdf(page_texts: list[str]) -> bytes:
    """Create a PDF with one page per string using Type1/Helvetica (WinAnsiEncoding)."""
    pdf = pikepdf.new()
    for text in page_texts:
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[-1]
        content_bytes = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET\n".encode("latin-1")
        page.Contents = pdf.make_indirect(pikepdf.Stream(pdf, content_bytes))
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


# --------------------------------------------------------------------------- #
# normalize()
# --------------------------------------------------------------------------- #

class TestNormalize:
    def test_nfc_precomposed_preserved(self):
        composed = "\xe9"  # é precomposed (NFC already)
        assert normalize(composed) == composed

    def test_nfc_decomposes_to_composed(self):
        decomposed = "é"  # e + combining acute accent -> é
        assert normalize(decomposed) == "\xe9"

    def test_strips_zero_width_space(self):
        assert normalize("hello​world") == "helloworld"

    def test_strips_zero_width_non_joiner(self):
        assert normalize("hello‌world") == "helloworld"

    def test_strips_zero_width_joiner(self):
        assert normalize("hello‍world") == "helloworld"

    def test_strips_soft_hyphen(self):
        assert normalize("co­operate") == "cooperate"

    def test_strips_all_zw_chars_combined(self):
        s = "​foo‌‍bar­baz"
        assert normalize(s) == "foobarbaz"

    def test_collapses_multiple_spaces(self):
        assert normalize("foo   bar") == "foo bar"

    def test_collapses_tab_and_newline(self):
        assert normalize("foo\t\nbar") == "foo bar"

    def test_collapses_mixed_whitespace(self):
        assert normalize("  foo  \t  bar\n\tbaz  ") == "foo bar baz"

    def test_trims_leading_whitespace(self):
        assert normalize("   hello") == "hello"

    def test_trims_trailing_whitespace(self):
        assert normalize("hello   ") == "hello"

    def test_empty_string(self):
        assert normalize("") == ""

    def test_whitespace_only(self):
        assert normalize("   \t\n  ") == ""

    def test_case_sensitive_unchanged(self):
        assert normalize("Hello WORLD") == "Hello WORLD"

    def test_no_mutation_of_non_matching(self):
        s = "plain ASCII text"
        assert normalize(s) == s


# --------------------------------------------------------------------------- #
# tokenize()
# --------------------------------------------------------------------------- #

class TestTokenize:
    def test_basic_split(self):
        assert tokenize("foo bar baz") == ["foo", "bar", "baz"]

    def test_empty_string(self):
        assert tokenize("") == []

    def test_whitespace_only(self):
        assert tokenize("   \t\n  ") == []

    def test_single_token(self):
        assert tokenize("hello") == ["hello"]

    def test_extra_whitespace_between_tokens(self):
        assert tokenize("foo   bar") == ["foo", "bar"]

    def test_preserves_case(self):
        assert tokenize("Hello WORLD") == ["Hello", "WORLD"]

    def test_tokens_with_digits_and_punctuation(self):
        assert tokenize("5,000 / 50000 / Rs1,20,000.00") == [
            "5,000", "/", "50000", "/", "Rs1,20,000.00"
        ]


# --------------------------------------------------------------------------- #
# extract_text_per_page()
# --------------------------------------------------------------------------- #

class TestExtractTextPerPage:
    def test_returns_list(self):
        data = _blank_pdf()
        result = extract_text_per_page(data)
        assert isinstance(result, list)

    def test_blank_single_page_returns_one_entry(self):
        data = _blank_pdf(page_count=1)
        result = extract_text_per_page(data)
        assert len(result) == 1

    def test_blank_three_pages_returns_three_entries(self):
        data = _blank_pdf(page_count=3)
        result = extract_text_per_page(data)
        assert len(result) == 3

    def test_blank_page_text_is_string(self):
        data = _blank_pdf()
        result = extract_text_per_page(data)
        assert isinstance(result[0], str)

    def test_text_pdf_single_page_contains_text(self):
        data = _text_pdf(["Hello World"])
        result = extract_text_per_page(data)
        assert len(result) == 1
        assert "Hello" in result[0]
        assert "World" in result[0]

    def test_text_pdf_multipage_each_page_has_text(self):
        data = _text_pdf(["First page", "Second page"])
        result = extract_text_per_page(data)
        assert len(result) == 2
        assert "First" in result[0]
        assert "Second" in result[1]

    def test_text_pdf_pages_are_independent(self):
        data = _text_pdf(["Alpha", "Beta"])
        result = extract_text_per_page(data)
        # Page 0 should not contain page 1's text and vice-versa
        assert "Beta" not in result[0]
        assert "Alpha" not in result[1]

    def test_empty_bytes_returns_empty_list(self):
        result = extract_text_per_page(b"")
        assert result == []

    def test_malformed_bytes_returns_empty_list(self):
        result = extract_text_per_page(b"not a pdf at all !!!!")
        assert result == []

    def test_does_not_raise_on_garbage_input(self):
        # Any exception must be swallowed; result is always a list
        result = extract_text_per_page(b"\x00\xff\xfe" * 100)
        assert isinstance(result, list)


# --------------------------------------------------------------------------- #
# extract_words_per_page() + WordBox
# --------------------------------------------------------------------------- #

class TestWordBox:
    def test_frozen(self):
        wb = WordBox(text="hello", x0=10.0, top=20.0, x1=50.0, bottom=35.0)
        with pytest.raises((AttributeError, TypeError)):
            wb.text = "changed"  # type: ignore[misc]

    def test_fields(self):
        wb = WordBox(text="test", x0=1.0, top=2.0, x1=3.0, bottom=4.0)
        assert wb.text == "test"
        assert wb.x0 == 1.0
        assert wb.top == 2.0
        assert wb.x1 == 3.0
        assert wb.bottom == 4.0


class TestExtractWordsPerPage:
    def test_returns_list(self):
        data = _blank_pdf()
        result = extract_words_per_page(data)
        assert isinstance(result, list)

    def test_blank_single_page_returns_one_entry(self):
        data = _blank_pdf(page_count=1)
        result = extract_words_per_page(data)
        assert len(result) == 1

    def test_blank_three_pages_returns_three_entries(self):
        data = _blank_pdf(page_count=3)
        result = extract_words_per_page(data)
        assert len(result) == 3

    def test_blank_page_words_is_list(self):
        data = _blank_pdf()
        result = extract_words_per_page(data)
        assert isinstance(result[0], list)

    def test_text_pdf_has_words(self):
        data = _text_pdf(["Hello World"])
        result = extract_words_per_page(data)
        assert len(result) == 1
        assert len(result[0]) > 0

    def test_text_pdf_words_are_wordbox(self):
        data = _text_pdf(["Hello World"])
        result = extract_words_per_page(data)
        for word in result[0]:
            assert isinstance(word, WordBox)

    def test_text_pdf_word_text_extracted(self):
        data = _text_pdf(["Hello World"])
        result = extract_words_per_page(data)
        texts = {w.text for w in result[0]}
        assert "Hello" in texts or "World" in texts  # at least one word present

    def test_wordbox_coordinates_are_floats(self):
        data = _text_pdf(["Test"])
        result = extract_words_per_page(data)
        if result and result[0]:
            wb = result[0][0]
            assert isinstance(wb.x0, float)
            assert isinstance(wb.top, float)
            assert isinstance(wb.x1, float)
            assert isinstance(wb.bottom, float)

    def test_wordbox_x0_less_than_x1(self):
        data = _text_pdf(["Test"])
        result = extract_words_per_page(data)
        if result and result[0]:
            wb = result[0][0]
            assert wb.x0 <= wb.x1

    def test_empty_bytes_returns_empty_list(self):
        result = extract_words_per_page(b"")
        assert result == []

    def test_malformed_bytes_returns_empty_list(self):
        result = extract_words_per_page(b"not a pdf at all !!!!")
        assert result == []

    def test_does_not_raise_on_garbage_input(self):
        result = extract_words_per_page(b"\x00\xff\xfe" * 100)
        assert isinstance(result, list)
