"""Locate + decode the ORIGINAL embedded raster images (Stage 6, §2).

The forensic methods must receive the *original* compressed / decoded image
XObject stream — **never** ``ctx.rasterized_pages()`` (the 300-DPI re-render).
Re-rasterisation is a second compression that erases the quantisation / JPEG-grid
evidence double-JPEG and ELA depend on. This module is the single decode path.

Responsibilities (extraction only — no scoring):

* Walk each page's ``/Resources/XObject`` for ``/Subtype /Image`` (recursing one
  level into form XObjects to catch nested images).
* Decode every image to an 8-bit grayscale/RGB ndarray via ``pikepdf.PdfImage``,
  which resolves DeviceGray/RGB/CMYK/ICCBased/Indexed, applies ``/Decode`` and
  composites ``/SMask``. CMYK and Indexed are normalised to RGB.
* For ``DCTDecode`` (JPEG) sources, ALSO retain the raw undecoded JPEG bytes
  verbatim (``read_raw_bytes``) — double-JPEG needs the original compressed
  stream, not a re-encoded array.
* Map each image back to its page-space placement rectangle (for the bbox
  overlay) by walking the content stream's CTM stack.
* Record a PHI-safe salted content hash, never the pixels.

Tolerant throughout: an undecodable image is recorded with ``pixels=None`` and a
note — it degrades the page, never the run. PHI: this module logs nothing; the
pixel arrays and raw bytes it returns are PHI and stay server-side.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import ImageForensicsConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

    from ..core.context import AnalysisContext

#: ``ctx.stage_cache`` key under which the decoded-image list is memoised.
_CACHE_KEY = "image_forensics.decoded"

#: 6-tuple identity matrix (PDF text/graphics matrix ``[a b c d e f]``).
_IDENTITY: tuple[float, float, float, float, float, float] = (1, 0, 0, 1, 0, 0)


@dataclass(frozen=True)
class DecodedImage:
    """One decoded embedded image XObject + everything the methods need.

    ``pixels`` is an 8-bit grayscale (``H×W``) or RGB (``H×W×3``) ndarray, or
    ``None`` if the image could not be decoded. ``jpeg_bytes`` holds the original
    JPEG file bytes verbatim when the source filter is ``DCTDecode`` (so the
    JPEG/DQ/ELA analysers see zero re-encode), else ``None``.

    ``placement`` is the page-space rectangle the image is painted into, in
    **pdfplumber top-left points** ``(x0, top0, x1, top1)`` — the same convention
    the overlay UI consumes — or ``None`` when the content stream could not be
    parsed (never a wrong box). ``page_width_pt`` / ``page_height_pt`` are the
    page's point dimensions, so the aggregate layer can normalise to ``[0,1]``.
    """

    page_index: int
    xobject_id: str                      # "<obj> <gen>"
    colorspace: str
    filters: tuple[str, ...]
    width: int
    height: int
    bits: int
    has_smask: bool
    content_hash: str                    # salted; PHI-safe id, NOT the pixels
    # ``pixels`` / ``jpeg_bytes`` are document pixels = PHI. Excluded from the
    # repr so an accidental ``repr(img)`` / log line can never leak them; the
    # PHI-safe id is ``content_hash`` (§8).
    pixels: "np.ndarray | None" = field(default=None, repr=False)
    jpeg_bytes: bytes | None = field(default=None, repr=False)
    placement: tuple[float, float, float, float] | None = None
    page_width_pt: float | None = None
    page_height_pt: float | None = None
    note: str | None = None              # why a step degraded (e.g. decode fail)

    @property
    def is_jpeg(self) -> bool:
        """True when the original source stream is a JPEG (DCTDecode)."""
        return self.jpeg_bytes is not None


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #

def decoded_images(
    ctx: "AnalysisContext", config: ImageForensicsConfig | None = None
) -> list[DecodedImage]:
    """Return every decoded embedded image for the document, cached on ``ctx``.

    Memoised under ``ctx.stage_cache`` so the (potentially expensive) decode runs
    once per file across the activation predicate and every method. Never raises:
    a total failure (no pikepdf doc) yields ``[]``.
    """
    cached = ctx.stage_cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    cfg = config or ImageForensicsConfig()
    images = extract_images(ctx.pdf_bytes, cfg)
    ctx.stage_cache[_CACHE_KEY] = images
    return images


def extract_images(
    pdf_bytes: bytes, config: ImageForensicsConfig | None = None
) -> list[DecodedImage]:
    """Locate + decode every embedded image in ``pdf_bytes`` (no context cache).

    Read-only; never raises. Used directly by tests and by :func:`decoded_images`.
    """
    cfg = config or ImageForensicsConfig()
    try:
        import pikepdf
    except Exception:
        return []
    try:
        from io import BytesIO

        pdf = pikepdf.open(BytesIO(pdf_bytes))
    except Exception:  # corrupt / encrypted-needs-password / unsupported
        return []
    try:
        out: list[DecodedImage] = []
        for page_index, page in enumerate(pdf.pages):
            out.extend(_images_on_page(page_index, page, cfg))
        return out
    except Exception:
        return []
    finally:
        try:
            pdf.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Per-page walk
# --------------------------------------------------------------------------- #

def _images_on_page(
    page_index: int, page: Any, cfg: ImageForensicsConfig
) -> list[DecodedImage]:
    page_w, page_h = _page_dims(page)
    placements = _placements_by_id(page)

    out: list[DecodedImage] = []
    for xobj, in_form in _iter_image_xobjects(page, cfg):
        try:
            oid = _objgen_str(xobj)
            # Nested-in-form images: placement requires composing the form's
            # matrix; we don't (yet) — record without a box rather than a wrong
            # one (§2: "never a wrong box").
            rect = None if in_form else _first_placement(placements, xobj, page_w, page_h)
            out.append(
                _decode_one(
                    xobj,
                    page_index=page_index,
                    page_w=page_w,
                    page_h=page_h,
                    placement=rect,
                    cfg=cfg,
                )
            )
        except Exception:
            # A single degenerate image never aborts the page.
            continue
    return out


def _iter_image_xobjects(page: Any, cfg: ImageForensicsConfig):
    """Yield ``(image_xobject, nested_in_form)`` for the page.

    Recurses one level into form XObjects when ``cfg.recurse_form_xobjects``.
    """
    seen: set[tuple[int, int]] = set()
    try:
        xobjects = page.Resources.XObject
    except Exception:
        return
    for _name, xobj in _items(xobjects):
        subtype = _name_str(xobj.get("/Subtype"))
        if subtype == "/Image":
            key = _objgen_tuple(xobj)
            if key not in seen:
                seen.add(key)
                yield xobj, False
        elif subtype == "/Form" and cfg.recurse_form_xobjects:
            try:
                inner = xobj.Resources.XObject
            except Exception:
                continue
            for _n2, inner_xobj in _items(inner):
                if _name_str(inner_xobj.get("/Subtype")) != "/Image":
                    continue
                key = _objgen_tuple(inner_xobj)
                if key not in seen:
                    seen.add(key)
                    yield inner_xobj, True


def _decode_one(
    xobj: Any,
    *,
    page_index: int,
    page_w: float | None,
    page_h: float | None,
    placement: tuple[float, float, float, float] | None,
    cfg: ImageForensicsConfig,
) -> DecodedImage:
    filters = _filter_chain(xobj)
    is_jpeg = "/DCTDecode" in filters or "DCTDecode" in filters
    width = int(xobj.get("/Width", 0) or 0)
    height = int(xobj.get("/Height", 0) or 0)
    bits = int(xobj.get("/BitsPerComponent", 8) or 8)
    has_smask = "/SMask" in xobj

    raw: bytes = b""
    try:
        raw = bytes(xobj.read_raw_bytes())
    except Exception:
        raw = b""
    content_hash = _content_hash(raw, cfg.image_hash_salt)

    jpeg_bytes = raw if (is_jpeg and raw) else None
    colorspace = "unknown"
    pixels = None
    note: str | None = None
    try:
        import numpy as np
        import pikepdf

        pim = pikepdf.PdfImage(xobj)
        colorspace = _colorspace_label(pim, xobj)
        pil = pim.as_pil_image()
        if pil.mode in ("CMYK", "YCCK"):
            pil = pil.convert("RGB")
        elif pil.mode in ("P", "PA"):
            pil = pil.convert("RGB")
        elif pil.mode not in ("L", "RGB"):
            pil = pil.convert("RGB")
        pixels = np.asarray(pil)
    except Exception as exc:  # unsupported / undecodable: degrade, never crash
        note = f"decode_failed:{type(exc).__name__}"

    return DecodedImage(
        page_index=page_index,
        xobject_id=_objgen_str(xobj),
        colorspace=colorspace,
        filters=filters,
        width=width,
        height=height,
        bits=bits,
        has_smask=has_smask,
        content_hash=content_hash,
        pixels=pixels,
        jpeg_bytes=jpeg_bytes,
        placement=placement,
        page_width_pt=page_w,
        page_height_pt=page_h,
        note=note,
    )


# --------------------------------------------------------------------------- #
# Placement — content-stream CTM walk (§2)
# --------------------------------------------------------------------------- #

def _placements_by_id(
    page: Any,
) -> dict[tuple[int, int], list[tuple[float, float, float, float]]]:
    """Map each image XObject (by objgen) to its user-space placement rect(s).

    Walks the page content stream tracking the graphics-state CTM stack
    (``cm`` / ``q`` / ``Q``); at every ``/<Name> Do`` snapshots the CTM and maps
    the unit square's corners through it. Returns user-space (bottom-left) rects.
    """
    out: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    try:
        import pikepdf

        name_to_id: dict[str, tuple[int, int]] = {}
        for name, xobj in _items(page.Resources.XObject):
            name_to_id[_name_str(name)] = _objgen_tuple(xobj)

        ctm: tuple[float, ...] = _IDENTITY
        stack: list[tuple[float, ...]] = []
        for instr in pikepdf.parse_content_stream(page):
            op = str(instr.operator)
            if op == "q":
                stack.append(ctm)
            elif op == "Q":
                if stack:
                    ctm = stack.pop()
            elif op == "cm":
                try:
                    m = tuple(float(o) for o in instr.operands)
                    if len(m) == 6:
                        ctm = _concat(m, ctm)
                except Exception:
                    continue
            elif op == "Do":
                try:
                    oid = name_to_id.get(_name_str(instr.operands[0]))
                except Exception:
                    oid = None
                if oid is not None:
                    out.setdefault(oid, []).append(_unit_square_bbox(ctm))
    except Exception:
        return out
    return out


def _first_placement(
    placements: dict[tuple[int, int], list[tuple[float, float, float, float]]],
    xobj: Any,
    page_w: float | None,
    page_h: float | None,
) -> tuple[float, float, float, float] | None:
    """First user-space placement of ``xobj`` converted to top-left points."""
    rects = placements.get(_objgen_tuple(xobj))
    if not rects or page_h is None:
        return None
    x0, y0, x1, y1 = rects[0]
    # User space (bottom-left origin) -> pdfplumber top-left.
    return (x0, page_h - y1, x1, page_h - y0)


def _unit_square_bbox(
    m: tuple[float, ...],
) -> tuple[float, float, float, float]:
    """Axis-aligned bbox of the unit square transformed by matrix ``m``."""
    a, b, c, d, e, f = m
    corners = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    xs = [a * x + c * y + e for x, y in corners]
    ys = [b * x + d * y + f for x, y in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _concat(m: tuple[float, ...], ctm: tuple[float, ...]) -> tuple[float, ...]:
    """PDF matrix concatenation for ``cm``: CTM' = m · CTM (3×3 affine)."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = ctm
    return (
        a * a2 + b * c2,
        a * b2 + b * d2,
        c * a2 + d * c2,
        c * b2 + d * d2,
        e * a2 + f * c2 + e2,
        e * b2 + f * d2 + f2,
    )


# --------------------------------------------------------------------------- #
# pikepdf helpers (tolerant of version/object-shape differences)
# --------------------------------------------------------------------------- #

def _items(obj: Any):
    """Iterate ``(key, value)`` of a pikepdf dictionary, tolerant of API shape."""
    try:
        return list(obj.items())
    except Exception:
        return []


def _name_str(value: Any) -> str:
    """Render a pikepdf ``/Name`` (or anything) to its ``"/Foo"`` string."""
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _objgen_tuple(xobj: Any) -> tuple[int, int]:
    try:
        og = xobj.objgen
        return (int(og[0]), int(og[1]))
    except Exception:
        return (id(xobj), 0)


def _objgen_str(xobj: Any) -> str:
    obj, gen = _objgen_tuple(xobj)
    return f"{obj} {gen}"


def _filter_chain(xobj: Any) -> tuple[str, ...]:
    """Normalise ``/Filter`` (Name or Array) to a tuple of ``"/Foo"`` strings."""
    filt = xobj.get("/Filter")
    if filt is None:
        return ()
    try:
        # Array of names.
        return tuple(_name_str(f) for f in filt)
    except TypeError:
        return (_name_str(filt),)


def _colorspace_label(pim: Any, xobj: Any) -> str:
    """Best-effort colour-space label, never raising."""
    for getter in (lambda: pim.colorspace, lambda: _name_str(xobj.get("/ColorSpace"))):
        try:
            label = getter()
            if label:
                return str(label)
        except Exception:
            continue
    return "unknown"


def _page_dims(page: Any) -> tuple[float | None, float | None]:
    """Page (width, height) in points from the MediaBox, resolving inheritance."""
    for getter in (lambda: page.mediabox, lambda: page.MediaBox):
        try:
            mb = getter()
            x0, y0, x1, y1 = (float(mb[0]), float(mb[1]), float(mb[2]), float(mb[3]))
            return (abs(x1 - x0), abs(y1 - y0))
        except Exception:
            continue
    return (None, None)


def _content_hash(data: bytes, salt: str) -> str:
    """Salted sha256 of the source bytes, truncated — a PHI-safe id."""
    return hashlib.sha256(salt.encode("utf-8") + data).hexdigest()[:16]


__all__ = ["DecodedImage", "decoded_images", "extract_images"]
