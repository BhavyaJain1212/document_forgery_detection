"""Object-level diff between consecutive reconstructed revisions.

Detects which PDF objects were overridden between two Revision objects and
classifies each into one of the seven change classes from the spec.

Classification priority (mirrors the scoring rubric):
    SIGNATURE  — /FT /Sig widget or /Type /Sig dictionary
    FORM_FILL  — widget field whose /V changed from absent/empty to non-empty
    FIELD_EDIT — widget field whose /V changed from one value to another
    OVERLAY    — /Annot with subtype Stamp/Redact, or rect overlapping text
    MARKUP     — other /Annot, or page dict structural change
    CONTENT    — page /Contents stream, Form XObject, or unclassified stream
    META       — /Metadata XMP stream, /Info dict, /Catalog or /Pages structural

Public entry points
-------------------
diff_objects(rev_a, rev_b)
    Compare two Revision objects and return an ObjectDiff.

classify_changed_object(...)
    Isolated classifier for one changed object, useful for direct tests.
"""

from __future__ import annotations

from io import BytesIO

import pikepdf

from ..extract.words import WordBox, extract_words_per_page
from ..models import ObjectChange, ObjectChangeClass, ObjectDiff, Revision


ObjGen = tuple[int, int]


# ---------------------------------------------------------------------------
# Serialization helpers (used for change detection only, not output)
# ---------------------------------------------------------------------------

def _obj_sig(obj: pikepdf.Object) -> bytes:
    """Bytes signature for an object used ONLY for change detection.

    Uses str(obj) which resolves to the actual content (not the indirect
    reference b'N 0 R' that unparse() returns for indirect objects).
    For streams, also appends read_raw_bytes() to catch any re-compression
    that keeps decoded content equal but changes compressed bytes.
    """
    try:
        content = str(obj).encode("utf-8", errors="replace")
    except Exception:
        content = b""
    try:
        if isinstance(obj, pikepdf.Stream):
            content += obj.read_raw_bytes()
    except Exception:
        pass
    return content


def _build_sig_table(pdf: pikepdf.Pdf) -> dict[ObjGen, bytes]:
    """Map each indirect object's (num, gen) to its signature bytes."""
    result: dict[ObjGen, bytes] = {}
    for obj in pdf.objects:
        try:
            result[obj.objgen] = _obj_sig(obj)
        except Exception:
            pass
    return result


def _build_obj_lookup(pdf: pikepdf.Pdf) -> dict[ObjGen, pikepdf.Object]:
    """Map each indirect object's (num, gen) to the live pikepdf Object."""
    result: dict[ObjGen, pikepdf.Object] = {}
    for obj in pdf.objects:
        try:
            result[obj.objgen] = obj
        except Exception:
            pass
    return result


def _changed_objgens(
    sig_a: dict[ObjGen, bytes],
    sig_b: dict[ObjGen, bytes],
) -> tuple[tuple[ObjGen, bool], ...]:
    """Return changed/new object IDs in the later revision.

    ``is_new`` is true when the object did not exist in the earlier revision.
    Objects that existed only in the earlier revision are intentionally ignored:
    the Stage 1 signal comes from objects present in the later reconstructed
    revision, including overridden objects with the same objgen and newly added
    objects referenced by overridden structures.
    """
    changed: list[tuple[ObjGen, bool]] = []
    for objgen in sorted(sig_b):
        is_new = objgen not in sig_a
        if is_new or sig_a[objgen] != sig_b[objgen]:
            changed.append((objgen, is_new))
    return tuple(changed)


def _values_equal(value_a: object, value_b: object) -> bool:
    """Best-effort equality for pikepdf values used by field classification."""
    if value_a is None and value_b is None:
        return True
    if value_a is None or value_b is None:
        return False
    try:
        return _obj_sig(value_a) == _obj_sig(value_b)  # type: ignore[arg-type]
    except Exception:
        return str(value_a) == str(value_b)


def _value_sig(value: object) -> bytes:
    """Small stable signature for dictionary entries such as /Contents."""
    if value is None:
        return b"<missing>"
    try:
        if isinstance(value, pikepdf.Array):
            parts = []
            for item in value:
                try:
                    parts.append(f"{item.objgen[0]} {item.objgen[1]} R")
                except Exception:
                    parts.append(str(item))
            return "|".join(parts).encode("utf-8", errors="replace")
    except Exception:
        pass
    try:
        og = value.objgen  # type: ignore[attr-defined]
        return f"{og[0]} {og[1]} R".encode()
    except Exception:
        pass
    try:
        return _obj_sig(value)  # type: ignore[arg-type]
    except Exception:
        return str(value).encode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Context collectors — build lookup tables from the PDF structure
# ---------------------------------------------------------------------------

def _collect_page_ids(pdf: pikepdf.Pdf) -> dict[ObjGen, int]:
    """Map page dictionary objgens to their 0-based page index."""
    result: dict[ObjGen, int] = {}
    for page_idx, page in enumerate(pdf.pages):
        try:
            result[page.objgen] = page_idx
        except Exception:
            pass
    return result


def _collect_content_ids(pdf: pikepdf.Pdf) -> dict[ObjGen, int]:
    """Map content stream objgens to their 0-based page index."""
    result: dict[ObjGen, int] = {}
    for page_idx, page in enumerate(pdf.pages):
        try:
            contents = page.get("/Contents")
            if contents is None:
                continue
            if isinstance(contents, pikepdf.Array):
                for item in contents:
                    try:
                        result[item.objgen] = page_idx
                    except Exception:
                        pass
            else:
                try:
                    result[contents.objgen] = page_idx
                except Exception:
                    pass
        except Exception:
            pass
    return result


def _collect_annot_ids(pdf: pikepdf.Pdf) -> dict[ObjGen, int]:
    """Map annotation objgens to their 0-based page index."""
    result: dict[ObjGen, int] = {}
    for page_idx, page in enumerate(pdf.pages):
        try:
            annots = page.get("/Annots")
            if annots is None:
                continue
            for annot in annots:
                try:
                    result[annot.objgen] = page_idx
                except Exception:
                    pass
        except Exception:
            pass
    return result


def _collect_field_ids(
    pdf: pikepdf.Pdf,
) -> tuple[set[ObjGen], set[ObjGen]]:
    """Walk AcroForm /Fields and return (sig_field_ids, form_field_ids).

    sig_field_ids  — fields/dicts with /FT /Sig (and their /V sig objects).
    form_field_ids — all other terminal fields that have /FT.
    """
    sig_ids: set[ObjGen] = set()
    form_ids: set[ObjGen] = set()
    try:
        acroform = pdf.Root.get("/AcroForm")
        if acroform is None:
            return sig_ids, form_ids
        raw_fields = acroform.get("/Fields")
        if raw_fields is None:
            return sig_ids, form_ids

        queue = list(raw_fields)
        visited: set[ObjGen] = set()
        while queue:
            item = queue.pop()
            try:
                og = item.objgen
            except Exception:
                continue
            if og in visited:
                continue
            visited.add(og)
            try:
                ft = item.get("/FT")
                if ft is not None:
                    if str(ft) == "/Sig":
                        sig_ids.add(og)
                        v = item.get("/V")
                        if v is not None:
                            try:
                                sig_ids.add(v.objgen)
                            except Exception:
                                pass
                    else:
                        form_ids.add(og)
                kids = item.get("/Kids")
                if kids is not None:
                    queue.extend(list(kids))
            except Exception:
                pass
    except Exception:
        pass
    return sig_ids, form_ids


def _collect_metadata_ids(pdf: pikepdf.Pdf) -> set[ObjGen]:
    """Collect /Metadata (XMP stream) and /Info (document info dict) objgens."""
    result: set[ObjGen] = set()
    try:
        meta = pdf.Root.get("/Metadata")
        if meta is not None:
            try:
                result.add(meta.objgen)
            except Exception:
                pass
    except Exception:
        pass
    try:
        info = pdf.trailer.get("/Info")
        if info is not None:
            try:
                result.add(info.objgen)
            except Exception:
                pass
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Geometry helper for OVERLAY detection
# ---------------------------------------------------------------------------

def _get_page_height(pdf: pikepdf.Pdf, page_idx: int) -> float:
    """Return page height in user units (MediaBox[3])."""
    try:
        page = pdf.pages[page_idx]
        for key in ("/MediaBox", "/CropBox"):
            box = page.get(key)
            if box is not None:
                return float(box[3])
    except Exception:
        pass
    return 792.0  # US Letter fallback


def _rect_overlaps_words(
    rect: pikepdf.Object, page_height: float, words: list[WordBox]
) -> bool:
    """True if the PDF annotation /Rect overlaps any word bounding box.

    Converts from PDF coordinates (bottom-left origin) to pdfplumber
    coordinates (top-left origin) before comparing.
    """
    if not words:
        return False
    try:
        vals = [float(v) for v in rect]
        if len(vals) < 4:
            return False
        x0 = min(vals[0], vals[2])
        y0 = min(vals[1], vals[3])
        x1 = max(vals[0], vals[2])
        y1 = max(vals[1], vals[3])
        # Convert PDF (bottom-left) -> pdfplumber (top-left)
        ann_top = page_height - y1
        ann_bottom = page_height - y0
        for w in words:
            # Horizontal separation
            if x1 <= w.x0 or w.x1 <= x0:
                continue
            # Vertical separation
            if ann_bottom <= w.top or w.bottom <= ann_top:
                continue
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _v_empty(v: object) -> bool:
    """True if a PDF form-field /V value should be treated as 'unfilled'."""
    if v is None:
        return True
    try:
        # Detect PDF null object
        if str(v).strip().lower() == "null":
            return True
        # Empty string: pikepdf represents () as empty bytes
        b = bytes(v)  # type: ignore[arg-type]
        return not b.strip()
    except Exception:
        pass
    try:
        s = str(v).strip()
        return s in ("", "()", "/", "b''")
    except Exception:
        return True


def _page_contents_changed(
    obj_a: pikepdf.Object | None,
    obj_b: pikepdf.Object,
) -> bool:
    """True if a /Page dictionary's /Contents entry changed."""
    try:
        if not isinstance(obj_b, pikepdf.Dictionary):
            return False
        if str(obj_b.get("/Type")) != "/Page":
            return False
        new_contents = obj_b.get("/Contents")
        old_contents = obj_a.get("/Contents") if obj_a is not None else None
        return not _values_equal(_value_sig(old_contents), _value_sig(new_contents))
    except Exception:
        return False


def classify_changed_object(
    objgen: ObjGen,
    obj_b: pikepdf.Object,
    obj_a: pikepdf.Object | None,
    page_ids: dict[ObjGen, int],
    content_ids: dict[ObjGen, int],
    annot_ids: dict[ObjGen, int],
    sig_ids: set[ObjGen],
    form_ids: set[ObjGen],
    metadata_ids: set[ObjGen],
    pdf_b: pikepdf.Pdf,
    word_pages: list[list[WordBox]],
) -> tuple[ObjectChangeClass, int | None, list[str]]:
    """Classify one changed object. Returns (class, page_index, notes)."""
    notes: list[str] = []
    page_idx: int | None = None

    # 1. Signature: known sig field / sig dict, or /Type /Sig
    if objgen in sig_ids:
        return ObjectChangeClass.SIGNATURE, None, notes
    try:
        if isinstance(obj_b, pikepdf.Dictionary):
            t = obj_b.get("/Type")
            if t is not None and str(t) == "/Sig":
                return ObjectChangeClass.SIGNATURE, None, notes
    except Exception:
        pass

    # 2. Widget with /FT — form field /V change
    if objgen in form_ids:
        try:
            new_v = obj_b.get("/V")
            old_v = obj_a.get("/V") if obj_a is not None else None
            if not _values_equal(old_v, new_v):
                if _v_empty(old_v) and not _v_empty(new_v):
                    return ObjectChangeClass.FORM_FILL, None, notes
                return ObjectChangeClass.FIELD_EDIT, None, notes
        except Exception as exc:
            notes.append(f"form-field /V comparison failed: {exc}")
        # Fall through: if /V didn't change, classify as annotation below

    # 3. Annotation (from page /Annots or by /Type /Annot)
    is_annot = objgen in annot_ids
    if is_annot:
        page_idx = annot_ids[objgen]
    if not is_annot:
        try:
            if isinstance(obj_b, pikepdf.Dictionary):
                t = obj_b.get("/Type")
                if t is not None and str(t) == "/Annot":
                    is_annot = True
        except Exception:
            pass

    if is_annot:
        try:
            sub = obj_b.get("/Subtype")
            subtype_str = str(sub) if sub is not None else ""
            if subtype_str in ("/Stamp", "/Redact"):
                return ObjectChangeClass.OVERLAY, page_idx, notes
            rect = obj_b.get("/Rect")
            if rect is not None and page_idx is not None:
                ph = _get_page_height(pdf_b, page_idx)
                wp = word_pages[page_idx] if page_idx < len(word_pages) else []
                if _rect_overlaps_words(rect, ph, wp):
                    return ObjectChangeClass.OVERLAY, page_idx, notes
        except Exception as exc:
            notes.append(f"annot subtype/rect check failed: {exc}")
        return ObjectChangeClass.MARKUP, page_idx, notes

    # 4. Content stream referenced from page /Contents
    if objgen in content_ids:
        page_idx = content_ids[objgen]
        return ObjectChangeClass.CONTENT, page_idx, notes

    # 5. Page dictionary whose /Contents entry changed -> CONTENT.
    if _page_contents_changed(obj_a, obj_b):
        page_idx = page_ids.get(objgen)
        return ObjectChangeClass.CONTENT, page_idx, notes

    # 6. Form XObject (text-bearing) — /Subtype /Form on a stream
    try:
        if isinstance(obj_b, pikepdf.Stream):
            sub = obj_b.get("/Subtype")
            if sub is not None and str(sub) == "/Form":
                return ObjectChangeClass.CONTENT, None, notes
    except Exception:
        pass

    # 7. Metadata: known metadata IDs, XMP streams, or /Type /Catalog//Pages
    if objgen in metadata_ids:
        return ObjectChangeClass.META, None, notes
    try:
        if isinstance(obj_b, pikepdf.Stream):
            sub = obj_b.get("/Subtype")
            if sub is not None and str(sub) == "/XML":
                return ObjectChangeClass.META, None, notes
    except Exception:
        pass
    try:
        if isinstance(obj_b, pikepdf.Dictionary):
            t = obj_b.get("/Type")
            if t is not None:
                ts = str(t)
                if ts in ("/Catalog", "/Pages"):
                    return ObjectChangeClass.META, None, notes
                if ts == "/Page":
                    # page_idx unknown at this point (obj not in annot_ids)
                    return ObjectChangeClass.MARKUP, None, notes
    except Exception:
        pass

    # 8. Fallback — streams are treated as CONTENT (safest for scoring)
    try:
        is_stream = isinstance(obj_b, pikepdf.Stream)
    except Exception:
        is_stream = False
    if is_stream:
        notes.append(f"unclassified stream {objgen}; treating as CONTENT")
    else:
        notes.append(f"unclassified object {objgen}; treating as CONTENT")
    return ObjectChangeClass.CONTENT, None, notes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diff_objects(rev_a: Revision, rev_b: Revision) -> ObjectDiff:
    """Compare two consecutive Revision objects and return classified object changes.

    Opens both with pikepdf, finds every indirect object whose serialised bytes
    differ (or that is new in rev_b), then classifies each change. All errors
    are captured as notes; the function never raises.
    """
    notes: list[str] = []
    changes: list[ObjectChange] = []

    try:
        with (
            pikepdf.open(BytesIO(rev_a.data)) as pdf_a,
            pikepdf.open(BytesIO(rev_b.data)) as pdf_b,
        ):
            sig_a = _build_sig_table(pdf_a)
            sig_b = _build_sig_table(pdf_b)
            lookup_a = _build_obj_lookup(pdf_a)
            lookup_b = _build_obj_lookup(pdf_b)

            page_ids = _collect_page_ids(pdf_b)
            content_ids = _collect_content_ids(pdf_b)
            annot_ids = _collect_annot_ids(pdf_b)
            sig_ids, form_ids = _collect_field_ids(pdf_b)
            metadata_ids = _collect_metadata_ids(pdf_b)
            word_pages = extract_words_per_page(rev_b.data)

            for objgen, is_new in _changed_objgens(sig_a, sig_b):
                obj_b = lookup_b.get(objgen)
                if obj_b is None:
                    notes.append(f"object {objgen} missing from lookup; skipped")
                    continue

                obj_a = lookup_a.get(objgen) if not is_new else None

                try:
                    cls, page_idx, obj_notes = classify_changed_object(
                        objgen=objgen,
                        obj_b=obj_b,
                        obj_a=obj_a,
                        page_ids=page_ids,
                        content_ids=content_ids,
                        annot_ids=annot_ids,
                        sig_ids=sig_ids,
                        form_ids=form_ids,
                        metadata_ids=metadata_ids,
                        pdf_b=pdf_b,
                        word_pages=word_pages,
                    )
                except Exception as exc:
                    notes.append(f"classification error for {objgen}: {exc}")
                    cls = ObjectChangeClass.CONTENT
                    page_idx = None
                    obj_notes = []

                changes.append(
                    ObjectChange(
                        obj_num=objgen[0],
                        gen_num=objgen[1],
                        change_class=cls,
                        page_index=page_idx,
                        is_new=is_new,
                        notes=tuple(obj_notes),
                    )
                )

    except pikepdf.PdfError as exc:
        notes.append(f"could not open revision for object diff: {exc}")
    except Exception as exc:
        notes.append(f"unexpected error during object diff: {exc}")

    return ObjectDiff(
        from_revision=rev_a.index,
        to_revision=rev_b.index,
        changes=tuple(changes),
        notes=tuple(notes),
    )
