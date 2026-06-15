"""Object-level diff between consecutive reconstructed revisions.

Detects which PDF objects were overridden between two Revision objects and
classifies each into one of the seven change classes from the spec.

How the changed-object SET is derived (important)
-------------------------------------------------
The set of objects "changed in revision k" is read from the cross-reference
records that revision k *physically authored* inside its own appended byte range
(``rev_b.data[len(rev_a.data):]``) — NOT by diffing two pikepdf object
enumerations. qpdf's enumeration of a *truncated* revision depends on what its
xref can resolve, so a truncated earlier revision can under-enumerate and make
thousands of pre-existing objects look "new". Reading the increment's own xref
(classic table, cross-reference stream, and any in-increment ``/XRefStm``)
avoids that entirely: an increment that writes no object records — e.g. the
184-byte hybrid/compatibility xref append that only adds an empty ``0 0``
subsection plus a back-pointing ``/XRefStm`` — falls out as *zero* changed
objects with no special-casing. See :func:`objects_written_in_increment`.

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

import re
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


# ---------------------------------------------------------------------------
# xref-driven changed-object set (the increment's own cross-reference records)
# ---------------------------------------------------------------------------

# A cross-reference table subsection header: ``<first> <count>``.
_SUBSEC_RE = re.compile(rb"(\d+)[ \t]+(\d+)[ \t]*\Z")
# A 20-byte classic xref entry: 10-digit offset, 5-digit gen, then ``n``/``f``.
_ENTRY_RE = re.compile(rb"(\d{10})[ \t]+(\d{5})[ \t]+([nf])[ \t]*\Z")
# ``startxref <offset>`` — the increment's primary cross-reference pointer.
_STARTXREF_RE = re.compile(rb"startxref\s+(\d+)")
# ``/XRefStm <offset>`` — a hybrid file's pointer to a cross-reference stream.
_XREFSTM_RE = re.compile(rb"/XRefStm\s+(\d+)")


def _last_int(pattern: re.Pattern[bytes], region: bytes) -> int | None:
    """Return the integer captured by the *last* match of ``pattern`` (or None)."""
    last: int | None = None
    for m in pattern.finditer(region):
        last = int(m.group(1))
    return last


def _objects_from_classic_table(raw: bytes, off: int) -> set[ObjGen]:
    """Parse a classic ``xref`` table at ``raw[off:]`` -> in-use ``(num, gen)``.

    Each subsection header ``<first> <count>`` is followed by ``count`` 20-byte
    entries; only type-``n`` (in-use) entries are collected. An empty ``0 0``
    subsection yields nothing. Parsing stops at ``trailer`` or the first line
    that is neither a header nor an entry. Never raises.
    """
    result: set[ObjGen] = set()
    head = re.match(rb"[ \t]*xref\b[ \t]*\r?\n?", raw[off : off + 32])
    if head is None:
        return result
    body = raw[off + head.end() :]
    tr = re.search(rb"\btrailer\b", body)
    if tr is not None:
        body = body[: tr.start()]
    lines = re.split(rb"\r\n|\r|\n", body)
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        hm = _SUBSEC_RE.match(line)
        if hm is None:
            break
        first, count = int(hm.group(1)), int(hm.group(2))
        for k in range(count):
            if i >= len(lines):
                break
            em = _ENTRY_RE.match(lines[i].strip())
            i += 1
            if em is not None and em.group(3) == b"n":
                result.add((first + k, int(em.group(2))))
    return result


def _decode_xref_rows(data: bytes, widths: list[int], index: list[int]) -> set[ObjGen]:
    """Decode an already-decompressed xref-stream payload -> in-use ``(num, gen)``.

    ``/W`` gives the three field widths; ``/Index`` lists ``(first, count)`` runs.
    Type-1 (in-use, uncompressed) and type-2 (compressed) records are collected;
    type-2 objects always have generation 0. Never raises.
    """
    result: set[ObjGen] = set()
    row_w = sum(widths)
    if row_w <= 0 or len(widths) < 1:
        return result
    pairs = list(zip(index[0::2], index[1::2]))
    pos = 0
    for first, count in pairs:
        for k in range(count):
            row = data[pos : pos + row_w]
            pos += row_w
            if len(row) < row_w:
                return result
            fields: list[int | None] = []
            o = 0
            for w in widths:
                fields.append(int.from_bytes(row[o : o + w], "big") if w > 0 else None)
                o += w
            typ = fields[0] if widths[0] > 0 else 1  # default type is 1 when W[0]==0
            if typ == 1:
                gen = fields[2] if (len(widths) > 2 and widths[2] > 0 and fields[2] is not None) else 0
                result.add((first + k, gen))
            elif typ == 2:
                result.add((first + k, 0))
    return result


def _objects_from_xref_stream(raw: bytes, off: int, end: int) -> set[ObjGen]:
    """Decode a cross-reference *stream* object whose definition starts at ``off``.

    The stream is loaded via pikepdf (which applies the stream filter + any PNG
    predictor), then its ``/W``/``/Index`` are decoded. The xref-stream object
    itself is included (its record is authored in this increment). Never raises.
    """
    result: set[ObjGen] = set()
    hm = re.match(rb"[ \t\r\n]*(\d+)[ \t]+(\d+)[ \t]+obj\b", raw[off : off + 48])
    if hm is None:
        return result
    num, gen = int(hm.group(1)), int(hm.group(2))
    try:
        with pikepdf.open(BytesIO(raw[:end])) as pdf:
            obj = pdf.get_object((num, gen))
            if obj is None or not isinstance(obj, pikepdf.Stream):
                return result
            if str(obj.get("/Type")) != "/XRef":
                return result
            widths = [int(x) for x in obj.get("/W")]
            size_obj = obj.get("/Size")
            size = int(size_obj) if size_obj is not None else 0
            idx = obj.get("/Index")
            index = [int(x) for x in idx] if idx is not None else [0, size]
            result |= _decode_xref_rows(obj.read_bytes(), widths, index)
            result.add((num, gen))
    except Exception:
        return result
    return result


def _objects_from_xref_at(raw: bytes, off: int, end: int) -> set[ObjGen]:
    """Dispatch a cross-reference at ``raw[off:]`` to the classic or stream reader."""
    if re.match(rb"[ \t]*xref\b", raw[off : off + 16]) is not None:
        return _objects_from_classic_table(raw, off)
    return _objects_from_xref_stream(raw, off, end)


def objects_written_in_increment(raw: bytes, start: int, end: int) -> set[ObjGen]:
    """Object ``(num, gen)`` ids whose xref record is authored within ``raw[start:end)``.

    This is the authoritative changed-object set for the revision whose bytes were
    appended in ``[start, end)``. It reads the increment's *own* cross-reference
    structure rather than diffing object enumerations:

    * The increment's closing ``startxref <offset>`` points at its primary xref —
      a classic table (parsed in place) or a cross-reference stream (decoded).
    * A ``/XRefStm <offset>`` in the increment's trailer is resolved **only** if
      the referenced stream physically lies within ``[start, end)``. A back-
      pointer into an earlier revision (``offset < start``) authored nothing here
      — this is exactly the hybrid/compatibility-append case, which therefore
      yields an empty set.

    Any cross-reference offset that falls outside ``[start, end)`` is ignored.
    Malformed input returns an empty set; the function never raises.
    """
    if not (0 <= start < end <= len(raw)):
        return set()
    region = raw[start:end]
    try:
        written: set[ObjGen] = set()
        for off in (_last_int(_STARTXREF_RE, region), _last_int(_XREFSTM_RE, region)):
            if off is None or not (start <= off < end):
                continue
            written |= _objects_from_xref_at(raw, off, end)
        return written
    except Exception:
        return set()


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

    # 7. Metadata: known metadata IDs, XMP streams, cross-reference / object
    #    streams (file infrastructure), or /Type /Catalog//Pages.
    if objgen in metadata_ids:
        return ObjectChangeClass.META, None, notes
    try:
        if isinstance(obj_b, pikepdf.Stream):
            sub = obj_b.get("/Subtype")
            if sub is not None and str(sub) == "/XML":
                return ObjectChangeClass.META, None, notes
            t = obj_b.get("/Type")
            # /XRef = cross-reference stream, /ObjStm = object stream container.
            # Both are structural plumbing, never document content.
            if t is not None and str(t) in ("/XRef", "/ObjStm"):
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

    The changed-object SET is the set of objects whose cross-reference record
    ``rev_b`` physically authored in its own appended byte range
    (``rev_b.data[len(rev_a.data):]``) — see
    :func:`objects_written_in_increment`. Each such object is then classified by
    inspecting its live form in ``rev_b`` (and its prior form in ``rev_a`` for
    form-field /V transitions). This avoids the enumeration-diff failure mode
    where a truncated earlier revision under-enumerates and floods the result
    with phantom "new" objects. All errors are captured as notes; never raises.
    """
    notes: list[str] = []
    changes: list[ObjectChange] = []

    # rev_a must be a byte-prefix of rev_b for an honest "increment" to exist.
    # In the real pipeline this always holds (both are truncations of the same
    # raw file). If it does not (e.g. two independently-saved PDFs), there is no
    # incremental relationship to read -- report and return nothing.
    start = len(rev_a.data)
    if rev_b.data[:start] != rev_a.data:
        notes.append(
            "revisions are not in an incremental (prefix) relationship; "
            "no increment to diff"
        )
        return ObjectDiff(
            from_revision=rev_a.index,
            to_revision=rev_b.index,
            changes=(),
            notes=tuple(notes),
        )

    written = objects_written_in_increment(rev_b.data, start, len(rev_b.data))
    if not written:
        notes.append(
            "increment authored no object records "
            "(compatibility/hybrid xref or cross-reference rebuild); no content change"
        )
        return ObjectDiff(
            from_revision=rev_a.index,
            to_revision=rev_b.index,
            changes=(),
            notes=tuple(notes),
        )

    try:
        with (
            pikepdf.open(BytesIO(rev_a.data)) as pdf_a,
            pikepdf.open(BytesIO(rev_b.data)) as pdf_b,
        ):
            page_ids = _collect_page_ids(pdf_b)
            content_ids = _collect_content_ids(pdf_b)
            annot_ids = _collect_annot_ids(pdf_b)
            sig_ids, form_ids = _collect_field_ids(pdf_b)
            metadata_ids = _collect_metadata_ids(pdf_b)
            word_pages = extract_words_per_page(rev_b.data)

            for objgen in sorted(written):
                try:
                    obj_b = pdf_b.get_object(objgen)
                except Exception as exc:
                    notes.append(f"could not resolve object {objgen} in later revision: {exc}")
                    continue
                if obj_b is None:
                    notes.append(f"object {objgen} written in increment but unresolved; skipped")
                    continue

                try:
                    obj_a = pdf_a.get_object(objgen)
                except Exception:
                    obj_a = None
                is_new = obj_a is None

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
