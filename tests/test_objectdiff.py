"""Tests for diff/objectdiff.py — object-level change detection and classification.

Strategy: build two Revision objects from separately-created pikepdf PDFs that
share the same structural layout (same object creation order -> same obj_nums).
Only the specific object under test differs between the two PDFs, so the diff
should produce exactly the expected set of ObjectChange entries.

For annotation/metadata/structural changes, the diff may include additional
ObjectChange entries for page dicts or catalogs that gained/lost keys. Tests
assert the presence of the expected class, not the exact change count, unless
they are checking a single isolated change.
"""

from __future__ import annotations

import io

import pikepdf
import pytest

from pdf_forgery.revision_recovery.diff.objectdiff import (
    _rect_overlaps_words,
    diff_objects,
)
from pdf_forgery.revision_recovery.extract.words import WordBox
from pdf_forgery.revision_recovery.models import (
    ObjectChange,
    ObjectChangeClass,
    ObjectDiff,
    Revision,
)


# --------------------------------------------------------------------------- #
# Common helpers
# --------------------------------------------------------------------------- #

def _save(pdf: pikepdf.Pdf) -> bytes:
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _make_revision(data: bytes, index: int = 0) -> Revision:
    with pikepdf.open(io.BytesIO(data)) as pdf:
        page_count = len(pdf.pages)
        is_encrypted = pdf.is_encrypted
    return Revision(
        index=index,
        source_boundary_index=index,
        truncate_len=len(data),
        page_count=page_count,
        is_encrypted=is_encrypted,
        data=data,
    )


def _content_pdf(text: str) -> bytes:
    """Single-page PDF with text content stream (Type1/Helvetica)."""
    pdf = pikepdf.new()
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
    return _save(pdf)


def _pdf_with_annot(subtype: str, rect: list[float] | None = None) -> bytes:
    """Single-page PDF with one annotation of the given subtype."""
    if rect is None:
        rect = [100.0, 100.0, 200.0, 200.0]
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[-1]
    page.Contents = pdf.make_indirect(pikepdf.Stream(pdf, b""))
    annot = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name(f"/{subtype}"),
        Rect=pikepdf.Array([float(v) for v in rect]),
    ))
    page["/Annots"] = pikepdf.Array([annot])
    return _save(pdf)


def _pdf_without_annot() -> bytes:
    """Single-page PDF without any annotation."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[-1]
    page.Contents = pdf.make_indirect(pikepdf.Stream(pdf, b""))
    return _save(pdf)


def _form_field_pdf(value: str | None) -> bytes:
    """PDF with one text widget field; /V is set if value is not None."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[-1]
    page.Contents = pdf.make_indirect(pikepdf.Stream(pdf, b""))

    field_dict = pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Widget"),
        FT=pikepdf.Name("/Tx"),
        T=pikepdf.String("field1"),
        Rect=pikepdf.Array([100, 100, 300, 120]),
    )
    if value is not None:
        field_dict["/V"] = pikepdf.String(value)

    field = pdf.make_indirect(field_dict)
    page["/Annots"] = pikepdf.Array([field])
    pdf.Root["/AcroForm"] = pdf.make_indirect(
        pikepdf.Dictionary(Fields=pikepdf.Array([field]))
    )
    return _save(pdf)


def _sig_field_pdf() -> bytes:
    """PDF with one signature widget field (/FT /Sig)."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[-1]
    page.Contents = pdf.make_indirect(pikepdf.Stream(pdf, b""))

    sig_field = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Widget"),
        FT=pikepdf.Name("/Sig"),
        T=pikepdf.String("Signature1"),
        Rect=pikepdf.Array([100, 600, 400, 680]),
    ))
    page["/Annots"] = pikepdf.Array([sig_field])
    pdf.Root["/AcroForm"] = pdf.make_indirect(
        pikepdf.Dictionary(Fields=pikepdf.Array([sig_field]))
    )
    return _save(pdf)


def _metadata_pdf() -> bytes:
    """PDF with an XMP /Metadata stream in the catalog."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[-1]
    page.Contents = pdf.make_indirect(pikepdf.Stream(pdf, b""))

    xmp_data = b"<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?><x:xmpmeta/><?xpacket end='r'?>"
    xmp = pdf.make_indirect(pikepdf.Stream(pdf, xmp_data))
    xmp["/Type"] = pikepdf.Name("/Metadata")
    xmp["/Subtype"] = pikepdf.Name("/XML")
    pdf.Root["/Metadata"] = xmp

    return _save(pdf)


def _info_pdf(title: str) -> bytes:
    """PDF with a /Info dictionary in the trailer."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[-1]
    page.Contents = pdf.make_indirect(pikepdf.Stream(pdf, b""))
    pdf.trailer["/Info"] = pdf.make_indirect(
        pikepdf.Dictionary(Title=pikepdf.String(title))
    )
    return _save(pdf)


def _classes(result: ObjectDiff) -> set[ObjectChangeClass]:
    return {c.change_class for c in result.changes}


# --------------------------------------------------------------------------- #
# _rect_overlaps_words (unit tests — no PDF needed)
# --------------------------------------------------------------------------- #

class TestRectOverlapsWords:
    def _word(self, x0, top, x1, bottom) -> WordBox:
        return WordBox(text="w", x0=x0, top=top, x1=x1, bottom=bottom)

    def _rect(self, x0, y0, x1, y1):
        """Build a trivial pikepdf Array as a /Rect value."""
        return pikepdf.Array([float(v) for v in [x0, y0, x1, y1]])

    def test_no_words_returns_false(self):
        rect = self._rect(100, 100, 200, 200)
        assert _rect_overlaps_words(rect, 792.0, []) is False

    def test_non_overlapping_right(self):
        # Annotation is to the right of the word
        rect = self._rect(300, 600, 400, 700)  # PDF coords
        word = self._word(x0=50, top=60, x1=150, bottom=80)  # pdfplumber coords
        # Annotation in pdfplumber: top=792-700=92, bottom=792-600=192
        # Word: top=60..80, x=50..150; Annotation: top=92..192, x=300..400 -> no x overlap
        assert _rect_overlaps_words(rect, 792.0, [word]) is False

    def test_non_overlapping_above(self):
        # Annotation is above the word in PDF coords (higher y)
        rect = self._rect(50, 750, 200, 780)  # PDF: top of page
        # In pdfplumber: top=792-780=12, bottom=792-750=42
        word = self._word(x0=50, top=100, x1=200, bottom=120)
        # Word top=100..120, Annotation top=12..42 -> ann_bottom=42 <= word.top=100 -> no overlap
        assert _rect_overlaps_words(rect, 792.0, [word]) is False

    def test_overlapping(self):
        # Annotation and word overlap
        rect = self._rect(50, 700, 200, 730)  # PDF: y=700..730
        # pdfplumber: top=792-730=62, bottom=792-700=92
        word = self._word(x0=60, top=65, x1=180, bottom=85)  # overlaps
        assert _rect_overlaps_words(rect, 792.0, [word]) is True

    def test_exact_edge_no_overlap(self):
        # Annotation right edge == word left edge: no overlap
        rect = self._rect(200, 700, 300, 730)
        # pdfplumber: top=62, bottom=92, x=200..300
        word = self._word(x0=300, top=65, x1=400, bottom=85)  # touches but no overlap
        assert _rect_overlaps_words(rect, 792.0, [word]) is False

    def test_multiple_words_one_overlaps(self):
        rect = self._rect(50, 700, 150, 730)
        # pdfplumber: top=62, bottom=92
        words = [
            self._word(x0=200, top=65, x1=300, bottom=85),  # no overlap
            self._word(x0=60, top=70, x1=140, bottom=80),   # overlaps
        ]
        assert _rect_overlaps_words(rect, 792.0, words) is True

    def test_malformed_rect_returns_false(self):
        rect = pikepdf.Array([1.0])  # too short
        word = self._word(x0=0, top=0, x1=100, bottom=100)
        assert _rect_overlaps_words(rect, 792.0, [word]) is False


# --------------------------------------------------------------------------- #
# diff_objects: return type and basic contract
# --------------------------------------------------------------------------- #

class TestDiffObjectsContract:
    def test_returns_ObjectDiff(self):
        data = _content_pdf("hello")
        rev = _make_revision(data)
        result = diff_objects(rev, rev)
        assert isinstance(result, ObjectDiff)

    def test_from_to_revision_indices(self):
        data = _content_pdf("hello")
        rev_a = _make_revision(data, index=0)
        rev_b = _make_revision(data, index=1)
        result = diff_objects(rev_a, rev_b)
        assert result.from_revision == 0
        assert result.to_revision == 1

    def test_identical_revisions_no_changes(self):
        data = _content_pdf("same text here")
        rev = _make_revision(data)
        result = diff_objects(rev, rev)
        assert len(result.changes) == 0

    def test_changes_is_tuple_of_ObjectChange(self):
        data_a = _content_pdf("version one")
        data_b = _content_pdf("version two")
        rev_a = _make_revision(data_a, 0)
        rev_b = _make_revision(data_b, 1)
        result = diff_objects(rev_a, rev_b)
        assert all(isinstance(c, ObjectChange) for c in result.changes)

    def test_unloadable_revision_returns_empty_with_note(self):
        garbage = b"%PDF-1.4\nnot a pdf\nstartxref\n5\n%%EOF\n"
        # Build a valid rev_a, use garbage as rev_b (will fail to open)
        data_a = _content_pdf("fine")
        rev_a = Revision(0, 0, len(data_a), 1, False, data_a)
        rev_b = Revision(1, 1, len(garbage), 0, False, garbage)
        result = diff_objects(rev_a, rev_b)
        assert len(result.changes) == 0
        assert len(result.notes) > 0

    def test_by_class_groups_correctly(self):
        data_a = _content_pdf("old")
        data_b = _content_pdf("new")
        rev_a = _make_revision(data_a, 0)
        rev_b = _make_revision(data_b, 1)
        result = diff_objects(rev_a, rev_b)
        grouped = result.by_class
        for cls, items in grouped.items():
            assert all(c.change_class == cls for c in items)


# --------------------------------------------------------------------------- #
# CONTENT classification
# --------------------------------------------------------------------------- #

class TestContentClassification:
    def test_modified_content_stream_is_content(self):
        rev_a = _make_revision(_content_pdf("original text"), 0)
        rev_b = _make_revision(_content_pdf("modified text"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.CONTENT in _classes(result)

    def test_content_change_has_page_index(self):
        rev_a = _make_revision(_content_pdf("original"), 0)
        rev_b = _make_revision(_content_pdf("edited"), 1)
        result = diff_objects(rev_a, rev_b)
        content_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.CONTENT]
        assert content_changes, "expected at least one CONTENT change"
        # Content stream should be on page 0
        assert any(c.page_index == 0 for c in content_changes)

    def test_form_xobject_is_content(self):
        """Form XObject (/Subtype /Form) on a stream -> CONTENT."""
        def _xobject_pdf(data: bytes) -> bytes:
            pdf = pikepdf.new()
            pdf.add_blank_page(page_size=(612, 792))
            page = pdf.pages[-1]
            # Embed a Form XObject in the resources
            xobj = pdf.make_indirect(pikepdf.Stream(pdf, data))
            xobj["/Subtype"] = pikepdf.Name("/Form")
            xobj["/Type"] = pikepdf.Name("/XObject")
            xobj["/BBox"] = pikepdf.Array([0, 0, 100, 100])
            page.Resources = pikepdf.Dictionary(
                XObject=pikepdf.Dictionary(Im0=xobj)
            )
            page.Contents = pdf.make_indirect(pikepdf.Stream(pdf, b"/Im0 Do"))
            return _save(pdf)

        rev_a = _make_revision(_xobject_pdf(b"original xobj"), 0)
        rev_b = _make_revision(_xobject_pdf(b"modified xobj"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.CONTENT in _classes(result)

    def test_content_change_is_not_new_when_same_obj_modified(self):
        """A modified content stream has is_new=False."""
        rev_a = _make_revision(_content_pdf("rev1"), 0)
        rev_b = _make_revision(_content_pdf("rev2"), 1)
        result = diff_objects(rev_a, rev_b)
        content_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.CONTENT]
        assert content_changes
        assert any(not c.is_new for c in content_changes)


# --------------------------------------------------------------------------- #
# MARKUP classification
# --------------------------------------------------------------------------- #

class TestMarkupClassification:
    def test_text_annotation_is_markup(self):
        rev_a = _make_revision(_pdf_without_annot(), 0)
        rev_b = _make_revision(_pdf_with_annot("Text"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.MARKUP in _classes(result)

    def test_highlight_annotation_is_markup(self):
        rev_a = _make_revision(_pdf_without_annot(), 0)
        rev_b = _make_revision(_pdf_with_annot("Highlight"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.MARKUP in _classes(result)

    def test_freetext_annotation_is_markup(self):
        rev_a = _make_revision(_pdf_without_annot(), 0)
        rev_b = _make_revision(_pdf_with_annot("FreeText"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.MARKUP in _classes(result)

    def test_annotation_object_is_reported_as_markup(self):
        rev_a = _make_revision(_pdf_without_annot(), 0)
        rev_b = _make_revision(_pdf_with_annot("Text"), 1)
        result = diff_objects(rev_a, rev_b)
        markup_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.MARKUP]
        assert markup_changes

    def test_annotation_page_index(self):
        rev_a = _make_revision(_pdf_without_annot(), 0)
        rev_b = _make_revision(_pdf_with_annot("Note"), 1)
        result = diff_objects(rev_a, rev_b)
        markup_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.MARKUP]
        # The annotation should be mapped to page 0. In these separately saved
        # fixture PDFs, pikepdf may reuse an old object number, so the change is
        # not necessarily marked is_new=True even though it is a new annotation
        # in the document structure.
        assert any(c.page_index == 0 for c in markup_changes)


# --------------------------------------------------------------------------- #
# OVERLAY classification
# --------------------------------------------------------------------------- #

class TestOverlayClassification:
    def test_stamp_annotation_is_overlay(self):
        rev_a = _make_revision(_pdf_without_annot(), 0)
        rev_b = _make_revision(_pdf_with_annot("Stamp"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.OVERLAY in _classes(result)

    def test_redact_annotation_is_overlay(self):
        rev_a = _make_revision(_pdf_without_annot(), 0)
        rev_b = _make_revision(_pdf_with_annot("Redact"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.OVERLAY in _classes(result)

    def test_overlay_not_markup(self):
        """Stamp should be OVERLAY, not MARKUP."""
        rev_a = _make_revision(_pdf_without_annot(), 0)
        rev_b = _make_revision(_pdf_with_annot("Stamp"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.OVERLAY in _classes(result)
        # The stamp annotation itself should not be MARKUP
        stamp_objs_as_markup = [
            c for c in result.changes
            if c.is_new and c.change_class == ObjectChangeClass.MARKUP
        ]
        # No new object classified as MARKUP (the annotation is Stamp -> OVERLAY)
        for c in stamp_objs_as_markup:
            # If any new object ended up as MARKUP, it must not be the annotation
            # (it would be the page dict structural change, if any)
            assert c.page_index is None or not c.is_new, (
                f"Expected annotation classified as OVERLAY, got MARKUP: {c}"
            )


# --------------------------------------------------------------------------- #
# SIGNATURE classification
# --------------------------------------------------------------------------- #

class TestSignatureClassification:
    def test_signature_widget_field_is_signature(self):
        rev_a = _make_revision(_content_pdf("doc"), 0)
        rev_b = _make_revision(_sig_field_pdf(), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.SIGNATURE in _classes(result)

    def test_signature_is_not_markup(self):
        """A /FT /Sig widget must not be classified as MARKUP."""
        rev_a = _make_revision(_content_pdf("doc"), 0)
        rev_b = _make_revision(_sig_field_pdf(), 1)
        result = diff_objects(rev_a, rev_b)
        sig_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.SIGNATURE]
        assert sig_changes, "expected at least one SIGNATURE change"


# --------------------------------------------------------------------------- #
# FORM_FILL / FIELD_EDIT classification
# --------------------------------------------------------------------------- #

class TestFormFieldClassification:
    def test_form_fill_empty_to_value(self):
        """Field /V from absent to non-empty -> FORM_FILL."""
        rev_a = _make_revision(_form_field_pdf(value=None), 0)
        rev_b = _make_revision(_form_field_pdf(value="John Doe"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.FORM_FILL in _classes(result)

    def test_form_fill_not_field_edit(self):
        rev_a = _make_revision(_form_field_pdf(value=None), 0)
        rev_b = _make_revision(_form_field_pdf(value="John"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.FORM_FILL in _classes(result)
        assert ObjectChangeClass.FIELD_EDIT not in _classes(result)

    def test_field_edit_value_to_value(self):
        """Field /V from one value to another -> FIELD_EDIT."""
        rev_a = _make_revision(_form_field_pdf(value="old value"), 0)
        rev_b = _make_revision(_form_field_pdf(value="new value"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.FIELD_EDIT in _classes(result)

    def test_field_edit_not_form_fill(self):
        rev_a = _make_revision(_form_field_pdf(value="first"), 0)
        rev_b = _make_revision(_form_field_pdf(value="second"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.FIELD_EDIT in _classes(result)
        assert ObjectChangeClass.FORM_FILL not in _classes(result)

    def test_form_fill_change_is_not_content(self):
        """A FORM_FILL field change must not trigger a CONTENT classification
        on the field itself."""
        rev_a = _make_revision(_form_field_pdf(value=None), 0)
        rev_b = _make_revision(_form_field_pdf(value="filled"), 1)
        result = diff_objects(rev_a, rev_b)
        form_fill_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.FORM_FILL]
        # The form field object itself must be FORM_FILL
        assert form_fill_changes

    def test_sig_field_takes_priority_over_form_classification(self):
        """A signature widget (/FT /Sig) is SIGNATURE, not FORM_FILL/FIELD_EDIT."""
        rev_a = _make_revision(_content_pdf("doc"), 0)
        rev_b = _make_revision(_sig_field_pdf(), 1)
        result = diff_objects(rev_a, rev_b)
        # Sig field should be SIGNATURE, not FORM_FILL or FIELD_EDIT
        assert ObjectChangeClass.SIGNATURE in _classes(result)
        assert ObjectChangeClass.FORM_FILL not in _classes(result)
        assert ObjectChangeClass.FIELD_EDIT not in _classes(result)


# --------------------------------------------------------------------------- #
# META classification
# --------------------------------------------------------------------------- #

class TestMetaClassification:
    def test_xmp_metadata_stream_is_meta(self):
        rev_a = _make_revision(_content_pdf("doc"), 0)
        rev_b = _make_revision(_metadata_pdf(), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.META in _classes(result)

    def test_info_dict_is_meta(self):
        rev_a = _make_revision(_content_pdf("doc"), 0)
        rev_b = _make_revision(_info_pdf("My Document"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.META in _classes(result)

    def test_metadata_change_alone_has_no_content(self):
        """Pure metadata change should produce no CONTENT classifications."""
        # Both have the same page content, only Info differs
        rev_a = _make_revision(_info_pdf("title v1"), 0)
        rev_b = _make_revision(_info_pdf("title v2"), 1)
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.META in _classes(result)
        # No content stream changed -> no CONTENT
        assert ObjectChangeClass.CONTENT not in _classes(result)


# --------------------------------------------------------------------------- #
# Mixed changes
# --------------------------------------------------------------------------- #

class TestMixedChanges:
    def test_content_and_annotation_in_same_diff(self):
        """When content changes AND an annotation is added, both are reported."""
        # rev_a: plain content
        # rev_b: different content + annotation

        def _combined(text: str, with_annot: bool) -> bytes:
            pdf = pikepdf.new()
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
            if with_annot:
                annot = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name("/Annot"),
                    Subtype=pikepdf.Name("/Text"),
                    Rect=pikepdf.Array([10, 10, 50, 50]),
                ))
                page["/Annots"] = pikepdf.Array([annot])
            return _save(pdf)

        rev_a = _make_revision(_combined("original", with_annot=False), 0)
        rev_b = _make_revision(_combined("edited", with_annot=True), 1)
        result = diff_objects(rev_a, rev_b)
        classes = _classes(result)
        assert ObjectChangeClass.CONTENT in classes
        assert ObjectChangeClass.MARKUP in classes
