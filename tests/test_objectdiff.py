"""Tests for diff/objectdiff.py — object-level change detection and classification.

The changed-object set is now derived from the cross-reference records each
revision *physically authored* in its own appended byte range (see
``objects_written_in_increment``), NOT from diffing two object enumerations.
Tests therefore build a genuine incremental update: a base PDF (rev_a) followed
by an appended revision that overrides/adds the specific object(s) under test
(rev_b), so ``rev_a.data`` is a true byte-prefix of ``rev_b.data`` — exactly what
"Save" (not "Save As") produces in a real editor. ``_append_increment`` writes
the new object(s) + a classic xref subsection + a ``/Prev``-chained trailer.
"""

from __future__ import annotations

import io
from io import BytesIO

import pikepdf
import pytest

from pdf_forgery.revision_recovery.detect import detect
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


def _append_increment(
    base: bytes,
    objects: dict[int, bytes],
    *,
    new_size: int | None = None,
    root: tuple[int, int] | None = None,
) -> bytes:
    """Append a genuine incremental update overriding/adding ``objects``.

    ``objects`` maps an object number (generation 0) to its raw body bytes — the
    text between ``"N 0 obj\\n"`` and ``"\\nendobj"``. The original bytes are kept
    verbatim as a prefix; only the new revision (objects + classic xref subsection
    + ``/Prev``-chained trailer + ``startxref`` + ``%%EOF``) is appended.
    """
    info_ref = ""
    with pikepdf.open(BytesIO(base)) as p:
        size = int(p.trailer.Size)
        if root is None:
            root = p.Root.objgen
        # The trailer is rewritten every revision; carry /Info forward so an
        # edited /Info dict is still the active document-info dict in rev_b.
        info = p.trailer.get("/Info")
        if info is not None:
            try:
                ig = info.objgen
                info_ref = f" /Info {ig[0]} {ig[1]} R"
            except Exception:
                info_ref = ""
    prev = detect(base).valid_boundaries[-1].startxref.pointer

    out = bytearray(base)
    if out and not out.endswith(b"\n"):
        out += b"\n"

    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode("latin-1") + objects[num] + b"\nendobj\n"

    xref_off = len(out)
    out += b"xref\n"
    for num in sorted(objects):
        out += f"{num} 1\n".encode("latin-1")
        out += f"{offsets[num]:010d} 00000 n \n".encode("latin-1")

    sz = new_size if new_size is not None else size
    out += b"trailer\n"
    out += f"<< /Size {sz} /Root {root[0]} {root[1]} R /Prev {prev}{info_ref} >>\n".encode("latin-1")
    out += b"startxref\n" + f"{xref_off}\n".encode("latin-1") + b"%%EOF\n"
    return bytes(out)


def _rev_pair(base: bytes, objects: dict[int, bytes], **kw) -> tuple[Revision, Revision]:
    """Build (rev_a, rev_b) where rev_b incrementally overrides ``objects``."""
    edited = _append_increment(base, objects, **kw)
    return _make_revision(base, 0), _make_revision(edited, 1)


# ---- body builders for the appended objects -------------------------------- #

def _content_body(text: str) -> bytes:
    s = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET\n".encode("latin-1")
    return f"<< /Length {len(s)} >>\nstream\n".encode("latin-1") + s + b"\nendstream"


def _stream_body(data: bytes, extra: str = "") -> bytes:
    return (
        f"<< /Length {len(data)} {extra}>>\nstream\n".encode("latin-1")
        + data
        + b"\nendstream"
    )


def _annot_body(subtype: str, rect: tuple[float, float, float, float]) -> bytes:
    x0, y0, x1, y1 = rect
    return (
        f"<< /Type /Annot /Subtype /{subtype} "
        f"/Rect [{x0} {y0} {x1} {y1}] >>"
    ).encode("latin-1")


def _field_body(value: str | None, *, ft: str = "/Tx", t: str = "field1") -> bytes:
    v = f" /V ({value})" if value is not None else ""
    return (
        f"<< /Type /Annot /Subtype /Widget /FT {ft} /T ({t}) "
        f"/Rect [100 100 300 120]{v} >>"
    ).encode("latin-1")


# ---- object-number lookups in a base PDF ----------------------------------- #

def _content_objnum(base: bytes) -> int:
    with pikepdf.open(BytesIO(base)) as p:
        return p.pages[0].Contents.objgen[0]


def _annot_objnum(base: bytes) -> int:
    with pikepdf.open(BytesIO(base)) as p:
        return p.pages[0]["/Annots"][0].objgen[0]


def _metadata_objnum(base: bytes) -> int:
    with pikepdf.open(BytesIO(base)) as p:
        return p.Root.Metadata.objgen[0]


def _info_objnum(base: bytes) -> int:
    with pikepdf.open(BytesIO(base)) as p:
        return p.trailer.Info.objgen[0]


# --------------------------------------------------------------------------- #
# Base PDF builders (these become rev_a; rev_b is an appended increment)
# --------------------------------------------------------------------------- #

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


def _xobject_pdf(data: bytes) -> bytes:
    """Single-page PDF whose page draws a Form XObject (/Subtype /Form)."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[-1]
    xobj = pdf.make_indirect(pikepdf.Stream(pdf, data))
    xobj["/Subtype"] = pikepdf.Name("/Form")
    xobj["/Type"] = pikepdf.Name("/XObject")
    xobj["/BBox"] = pikepdf.Array([0, 0, 100, 100])
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xobj))
    page.Contents = pdf.make_indirect(pikepdf.Stream(pdf, b"/Im0 Do"))
    return _save(pdf)


def _xobject_objnum(base: bytes) -> int:
    with pikepdf.open(BytesIO(base)) as p:
        return p.pages[0].Resources.XObject.Im0.objgen[0]


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
        rect = self._rect(300, 600, 400, 700)
        word = self._word(x0=50, top=60, x1=150, bottom=80)
        assert _rect_overlaps_words(rect, 792.0, [word]) is False

    def test_non_overlapping_above(self):
        rect = self._rect(50, 750, 200, 780)
        word = self._word(x0=50, top=100, x1=200, bottom=120)
        assert _rect_overlaps_words(rect, 792.0, [word]) is False

    def test_overlapping(self):
        rect = self._rect(50, 700, 200, 730)
        word = self._word(x0=60, top=65, x1=180, bottom=85)
        assert _rect_overlaps_words(rect, 792.0, [word]) is True

    def test_exact_edge_no_overlap(self):
        rect = self._rect(200, 700, 300, 730)
        word = self._word(x0=300, top=65, x1=400, bottom=85)
        assert _rect_overlaps_words(rect, 792.0, [word]) is False

    def test_multiple_words_one_overlaps(self):
        rect = self._rect(50, 700, 150, 730)
        words = [
            self._word(x0=200, top=65, x1=300, bottom=85),
            self._word(x0=60, top=70, x1=140, bottom=80),
        ]
        assert _rect_overlaps_words(rect, 792.0, words) is True

    def test_malformed_rect_returns_false(self):
        rect = pikepdf.Array([1.0])
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
        base = _content_pdf("hello")
        cnum = _content_objnum(base)
        rev_a, rev_b = _rev_pair(base, {cnum: _content_body("hello there")})
        result = diff_objects(rev_a, rev_b)
        assert result.from_revision == 0
        assert result.to_revision == 1

    def test_identical_revisions_no_changes(self):
        data = _content_pdf("same text here")
        rev = _make_revision(data)
        result = diff_objects(rev, rev)
        assert len(result.changes) == 0

    def test_changes_is_tuple_of_ObjectChange(self):
        base = _content_pdf("version one")
        cnum = _content_objnum(base)
        rev_a, rev_b = _rev_pair(base, {cnum: _content_body("version two")})
        result = diff_objects(rev_a, rev_b)
        assert isinstance(result.changes, tuple)
        assert result.changes
        assert all(isinstance(c, ObjectChange) for c in result.changes)

    def test_non_incremental_pair_yields_no_changes(self):
        """Two independently-saved PDFs are not in a prefix relationship, so
        there is no increment to read -> empty result with an explanatory note."""
        rev_a = _make_revision(_content_pdf("alpha"), 0)
        rev_b = _make_revision(_content_pdf("beta"), 1)
        result = diff_objects(rev_a, rev_b)
        assert result.changes == ()
        assert any("prefix" in n for n in result.notes)

    def test_unloadable_revision_returns_empty_with_note(self):
        garbage = b"%PDF-1.4\nnot a pdf\nstartxref\n5\n%%EOF\n"
        data_a = _content_pdf("fine")
        rev_a = Revision(0, 0, len(data_a), 1, False, data_a)
        rev_b = Revision(1, 1, len(garbage), 0, False, garbage)
        result = diff_objects(rev_a, rev_b)
        assert len(result.changes) == 0
        assert len(result.notes) > 0

    def test_by_class_groups_correctly(self):
        base = _content_pdf("old")
        cnum = _content_objnum(base)
        rev_a, rev_b = _rev_pair(base, {cnum: _content_body("new")})
        result = diff_objects(rev_a, rev_b)
        grouped = result.by_class
        for cls, items in grouped.items():
            assert all(c.change_class == cls for c in items)


# --------------------------------------------------------------------------- #
# CONTENT classification
# --------------------------------------------------------------------------- #

class TestContentClassification:
    def test_modified_content_stream_is_content(self):
        base = _content_pdf("original text")
        cnum = _content_objnum(base)
        rev_a, rev_b = _rev_pair(base, {cnum: _content_body("modified text")})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.CONTENT in _classes(result)

    def test_content_change_has_page_index(self):
        base = _content_pdf("original")
        cnum = _content_objnum(base)
        rev_a, rev_b = _rev_pair(base, {cnum: _content_body("edited")})
        result = diff_objects(rev_a, rev_b)
        content_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.CONTENT]
        assert content_changes, "expected at least one CONTENT change"
        assert any(c.page_index == 0 for c in content_changes)

    def test_form_xobject_is_content(self):
        """Form XObject (/Subtype /Form) on a stream -> CONTENT."""
        base = _xobject_pdf(b"original xobj")
        xnum = _xobject_objnum(base)
        rev_a, rev_b = _rev_pair(
            base,
            {xnum: _stream_body(b"modified xobj", extra="/Subtype /Form /Type /XObject /BBox [0 0 100 100] ")},
        )
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.CONTENT in _classes(result)

    def test_content_change_is_not_new_when_same_obj_modified(self):
        """A modified (overridden) content stream has is_new=False."""
        base = _content_pdf("rev1")
        cnum = _content_objnum(base)
        rev_a, rev_b = _rev_pair(base, {cnum: _content_body("rev2")})
        result = diff_objects(rev_a, rev_b)
        content_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.CONTENT]
        assert content_changes
        assert any(not c.is_new for c in content_changes)


# --------------------------------------------------------------------------- #
# MARKUP classification
# --------------------------------------------------------------------------- #

class TestMarkupClassification:
    @pytest.mark.parametrize("subtype", ["Text", "Highlight", "FreeText", "Note"])
    def test_comment_annotation_is_markup(self, subtype):
        base = _pdf_with_annot(subtype)
        anum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {anum: _annot_body(subtype, (100, 100, 200, 201))})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.MARKUP in _classes(result)

    def test_annotation_object_is_reported_as_markup(self):
        base = _pdf_with_annot("Text")
        anum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {anum: _annot_body("Text", (100, 100, 200, 201))})
        result = diff_objects(rev_a, rev_b)
        markup_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.MARKUP]
        assert markup_changes

    def test_annotation_page_index(self):
        base = _pdf_with_annot("Note")
        anum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {anum: _annot_body("Note", (100, 100, 200, 201))})
        result = diff_objects(rev_a, rev_b)
        markup_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.MARKUP]
        assert any(c.page_index == 0 for c in markup_changes)


# --------------------------------------------------------------------------- #
# OVERLAY classification
# --------------------------------------------------------------------------- #

class TestOverlayClassification:
    def test_stamp_annotation_is_overlay(self):
        base = _pdf_with_annot("Stamp")
        anum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {anum: _annot_body("Stamp", (100, 100, 200, 201))})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.OVERLAY in _classes(result)

    def test_redact_annotation_is_overlay(self):
        base = _pdf_with_annot("Redact")
        anum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {anum: _annot_body("Redact", (100, 100, 200, 201))})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.OVERLAY in _classes(result)

    def test_overlay_not_markup(self):
        """An overridden Stamp annotation is OVERLAY, never MARKUP."""
        base = _pdf_with_annot("Stamp")
        anum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {anum: _annot_body("Stamp", (100, 100, 200, 201))})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.OVERLAY in _classes(result)
        assert ObjectChangeClass.MARKUP not in _classes(result)


# --------------------------------------------------------------------------- #
# SIGNATURE classification
# --------------------------------------------------------------------------- #

class TestSignatureClassification:
    def test_signature_widget_field_is_signature(self):
        base = _sig_field_pdf()
        snum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {snum: _field_body(None, ft="/Sig", t="Signature1")})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.SIGNATURE in _classes(result)

    def test_signature_is_not_markup(self):
        """A /FT /Sig widget must not be classified as MARKUP."""
        base = _sig_field_pdf()
        snum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {snum: _field_body(None, ft="/Sig", t="Signature1")})
        result = diff_objects(rev_a, rev_b)
        sig_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.SIGNATURE]
        assert sig_changes, "expected at least one SIGNATURE change"


# --------------------------------------------------------------------------- #
# FORM_FILL / FIELD_EDIT classification
# --------------------------------------------------------------------------- #

class TestFormFieldClassification:
    def test_form_fill_empty_to_value(self):
        """Field /V from absent to non-empty -> FORM_FILL."""
        base = _form_field_pdf(value=None)
        fnum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {fnum: _field_body("John Doe")})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.FORM_FILL in _classes(result)

    def test_form_fill_not_field_edit(self):
        base = _form_field_pdf(value=None)
        fnum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {fnum: _field_body("John")})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.FORM_FILL in _classes(result)
        assert ObjectChangeClass.FIELD_EDIT not in _classes(result)

    def test_field_edit_value_to_value(self):
        """Field /V from one value to another -> FIELD_EDIT."""
        base = _form_field_pdf(value="old value")
        fnum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {fnum: _field_body("new value")})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.FIELD_EDIT in _classes(result)

    def test_field_edit_not_form_fill(self):
        base = _form_field_pdf(value="first")
        fnum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {fnum: _field_body("second")})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.FIELD_EDIT in _classes(result)
        assert ObjectChangeClass.FORM_FILL not in _classes(result)

    def test_form_fill_change_is_not_content(self):
        """A FORM_FILL field change must not be classified CONTENT."""
        base = _form_field_pdf(value=None)
        fnum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {fnum: _field_body("filled")})
        result = diff_objects(rev_a, rev_b)
        form_fill_changes = [c for c in result.changes if c.change_class == ObjectChangeClass.FORM_FILL]
        assert form_fill_changes
        assert ObjectChangeClass.CONTENT not in _classes(result)

    def test_sig_field_takes_priority_over_form_classification(self):
        """A signature widget (/FT /Sig) is SIGNATURE, not FORM_FILL/FIELD_EDIT."""
        base = _sig_field_pdf()
        snum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(base, {snum: _field_body(None, ft="/Sig", t="Signature1")})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.SIGNATURE in _classes(result)
        assert ObjectChangeClass.FORM_FILL not in _classes(result)
        assert ObjectChangeClass.FIELD_EDIT not in _classes(result)


# --------------------------------------------------------------------------- #
# META classification
# --------------------------------------------------------------------------- #

class TestMetaClassification:
    def test_xmp_metadata_stream_is_meta(self):
        base = _metadata_pdf()
        mnum = _metadata_objnum(base)
        new_xmp = b"<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?><x:xmpmeta>edited</x:xmpmeta><?xpacket end='r'?>"
        rev_a, rev_b = _rev_pair(
            base, {mnum: _stream_body(new_xmp, extra="/Type /Metadata /Subtype /XML ")}
        )
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.META in _classes(result)

    def test_info_dict_is_meta(self):
        base = _info_pdf("My Document")
        inum = _info_objnum(base)
        rev_a, rev_b = _rev_pair(base, {inum: b"<< /Title (Edited Title) >>"})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.META in _classes(result)

    def test_metadata_change_alone_has_no_content(self):
        """Pure metadata change should produce no CONTENT classifications."""
        base = _info_pdf("title v1")
        inum = _info_objnum(base)
        rev_a, rev_b = _rev_pair(base, {inum: b"<< /Title (title v2) >>"})
        result = diff_objects(rev_a, rev_b)
        assert ObjectChangeClass.META in _classes(result)
        assert ObjectChangeClass.CONTENT not in _classes(result)


# --------------------------------------------------------------------------- #
# Mixed changes
# --------------------------------------------------------------------------- #

class TestMixedChanges:
    def test_content_and_annotation_in_same_diff(self):
        """When the content stream AND an annotation are both overridden in one
        increment, both classes are reported."""
        # Base: content + a Text annotation.
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[-1]
        page.Contents = pdf.make_indirect(
            pikepdf.Stream(pdf, b"BT /F1 12 Tf 72 720 Td (original) Tj ET\n")
        )
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
        annot = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Text"),
            Rect=pikepdf.Array([10, 10, 50, 50]),
        ))
        page["/Annots"] = pikepdf.Array([annot])
        base = _save(pdf)

        cnum = _content_objnum(base)
        anum = _annot_objnum(base)
        rev_a, rev_b = _rev_pair(
            base,
            {
                cnum: _content_body("edited"),
                anum: _annot_body("Text", (10, 10, 50, 51)),
            },
        )
        result = diff_objects(rev_a, rev_b)
        classes = _classes(result)
        assert ObjectChangeClass.CONTENT in classes
        assert ObjectChangeClass.MARKUP in classes
