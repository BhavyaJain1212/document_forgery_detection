"""Orchestration for Stage 3 (OCR ↔ embedded-text cross-check).

Wires the GPU and CPU halves together:

  GPU queue:  rasterise each page (``pypdfium2`` via ``AnalysisContext`` /
              ``render_dpi``) → OCR recognition (``OCREngine.recognize``).
  CPU queue:  embedded extraction (``core.glyphs`` over ``ctx.page_layouts``) →
              transform + match (``align``) → guards → routing → classify
              (``divergence``) → score (``scoring``).

Produces an :class:`OCRCrossCheckReport`. Like every stage it is READ-ONLY and
MUST NOT raise on bad input — a missing OCR/raster backend, an unreadable file,
or an empty text layer degrades to an INCONCLUSIVE report with a note.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pdfminer.layout import LTPage

from ..core.types import ConfidenceTier
from .align import extract_embedded_words, match_words
from .config import OCRCrossCheckConfig
from .divergence import classify_page
from .guards import filter_low_confidence_ocr, filter_offpage_embedded
from .models import OCRCrossCheckReport, Stage3Result, WordBox
from .ocr_engine import OCREngine, PaddleOCREngine
from .routing import is_text_sparse
from .scoring import score


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def analyze_bytes(
    pdf_bytes: bytes,
    path: str = "<bytes>",
    config: OCRCrossCheckConfig | None = None,
    *,
    ctx: object | None = None,
    engine: OCREngine | None = None,
) -> OCRCrossCheckReport:
    """Cross-check embedded text vs OCR over ``pdf_bytes`` → report (never raises).

    ``ctx`` is the shared ``AnalysisContext`` (reused for ``page_layouts`` and
    rasterisation so the file is parsed once); ``engine`` overrides the default
    PaddleOCR backend (e.g. for tests). When the OCR engine or raster backend is
    unavailable the report is INCONCLUSIVE with a note rather than an error.
    """
    cfg = config or OCRCrossCheckConfig()
    try:
        return _run(pdf_bytes, path, cfg, ctx=ctx, engine=engine)
    except Exception as exc:
        return OCRCrossCheckReport(
            path=path,
            ok=False,
            result=None,
            error=f"unexpected error: {exc}",
            notes=(f"analyze_bytes failed: {type(exc).__name__}: {exc}",),
        )


def analyze_path(
    path: str,
    config: OCRCrossCheckConfig | None = None,
    *,
    engine: OCREngine | None = None,
) -> OCRCrossCheckReport:
    """Read a PDF (read-only) and run the cross-check → report (never raises)."""
    cfg = config or OCRCrossCheckConfig()
    try:
        with open(path, "rb") as fh:
            pdf_bytes = fh.read()
    except Exception as exc:
        return OCRCrossCheckReport(
            path=path,
            ok=False,
            result=None,
            error=f"could not read file: {exc}",
            notes=(f"analyze_path read failed: {exc}",),
        )
    return analyze_bytes(pdf_bytes, path, cfg, engine=engine)


# --------------------------------------------------------------------------- #
# Internal implementation                                                      #
# --------------------------------------------------------------------------- #

def _ok_inconclusive(
    path: str, note: str, *extra_notes: str
) -> OCRCrossCheckReport:
    return OCRCrossCheckReport(
        path=path,
        ok=True,
        result=Stage3Result(
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            divergences=(),
            routed_to=None,
        ),
        notes=(note,) + extra_notes,
    )


def _run(
    pdf_bytes: bytes,
    path: str,
    cfg: OCRCrossCheckConfig,
    *,
    ctx: object | None,
    engine: OCREngine | None,
) -> OCRCrossCheckReport:
    # ------------------------------------------------------------------ #
    # Step 1 — rasterise pages                                           #
    # ------------------------------------------------------------------ #
    raster_pages: list[bytes] = []
    if ctx is not None:
        # Reuse the shared AnalysisContext cache at our required DPI.
        try:
            raster_pages = ctx.rasterized_pages(cfg.render_dpi)  # type: ignore[union-attr]
        except Exception:
            pass
    if not raster_pages:
        raster_pages = _rasterize_direct(pdf_bytes, cfg.render_dpi)
    if not raster_pages:
        return _ok_inconclusive(
            path,
            "pypdfium2 not available or rasterization failed; "
            "install pypdfium2 for OCR cross-check",
        )

    # ------------------------------------------------------------------ #
    # Step 2 — OCR engine availability check                             #
    # ------------------------------------------------------------------ #
    ocr_engine = engine or PaddleOCREngine(cfg)
    _avail = getattr(ocr_engine, "is_available", None)
    if _avail is not None and not _avail():
        return _ok_inconclusive(
            path,
            "OCR engine not available (PaddleOCR not installed); "
            "install paddleocr for OCR cross-check",
        )

    # ------------------------------------------------------------------ #
    # Step 3 — page layouts (pdfminer)                                   #
    # ------------------------------------------------------------------ #
    page_layouts: list[LTPage] = []
    if ctx is not None:
        try:
            page_layouts = ctx.page_layouts  # type: ignore[union-attr]
        except Exception:
            pass
    if not page_layouts:
        page_layouts = _extract_layouts(pdf_bytes)
    if not page_layouts:
        return _ok_inconclusive(path, "no page layouts extracted from PDF")

    # ------------------------------------------------------------------ #
    # Step 4 — page rotations                                            #
    # ------------------------------------------------------------------ #
    page_rotations = _get_rotations(pdf_bytes, ctx)

    # ------------------------------------------------------------------ #
    # Step 5 — extract embedded words (all pages, pixel space)           #
    # ------------------------------------------------------------------ #
    all_embedded_raw = extract_embedded_words(page_layouts, cfg.render_dpi, page_rotations)

    # ------------------------------------------------------------------ #
    # Step 6 — per-page OCR + clipping guard + confidence floor         #
    # ------------------------------------------------------------------ #
    diag: dict[str, int] = {
        "offpage_dropped": 0,
        "low_conf_dropped": 0,
        "matched": 0,
        "agree": 0,
        "mismatch": 0,
    }
    page_embedded: list[list[WordBox]] = []
    page_ocr: list[list[WordBox]] = []
    page_dims_px_list: list[tuple[float, float]] = []

    n_pages = min(len(raster_pages), len(page_layouts))

    for pi in range(n_pages):
        # Embedded words for this page, filtered by clipping guard.
        scale = cfg.render_dpi / 72.0
        rot = page_rotations[pi] if pi < len(page_rotations) else 0
        pw, ph = _page_px_dims(page_layouts[pi], scale, rot)
        if cfg.enable_localization:
            page_dims_px_list.append((pw, ph))

        emb_raw = [w for w in all_embedded_raw if w.page_index == pi]
        emb_page, off_dropped = filter_offpage_embedded(
            emb_raw, page_width_px=pw, page_height_px=ph, config=cfg
        )
        diag["offpage_dropped"] += off_dropped

        # OCR this page.
        if hasattr(ocr_engine, "set_page"):
            ocr_engine.set_page(pi)  # type: ignore[union-attr]
        ocr_raw = ocr_engine.recognize(raster_pages[pi], dpi=cfg.render_dpi)
        # Tag with page_index (engine returns page_index=0 by convention).
        ocr_raw = [
            w.__class__(
                text=w.text, bbox=w.bbox, source=w.source, conf=w.conf, page_index=pi
            )
            for w in ocr_raw
        ]
        ocr_page, conf_dropped = filter_low_confidence_ocr(ocr_raw, cfg)
        diag["low_conf_dropped"] += conf_dropped

        page_embedded.append(emb_page)
        page_ocr.append(ocr_page)

    # ------------------------------------------------------------------ #
    # Step 7 — scanned / text-sparse routing (whole-document totals)    #
    # ------------------------------------------------------------------ #
    all_embedded_flat = [w for pg in page_embedded for w in pg]
    all_ocr_flat = [w for pg in page_ocr for w in pg]

    if is_text_sparse(all_embedded_flat, all_ocr_flat, cfg):
        result = Stage3Result(
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            divergences=(),
            routed_to=cfg.image_forensics_route,
        )
        try:
            prov = ocr_engine.provenance()
        except Exception:
            prov = None
        return OCRCrossCheckReport(
            path=path,
            ok=True,
            result=result,
            provenance=prov,
            diagnostics=dict(diag),
            notes=(
                f"scanned/text-sparse: {len(all_embedded_flat)} embedded, "
                f"{len(all_ocr_flat)} OCR words — routed to {cfg.image_forensics_route}",
            ),
            page_dims_px=tuple(page_dims_px_list),
        )

    # ------------------------------------------------------------------ #
    # Step 8 — per-page match + classify                                 #
    # ------------------------------------------------------------------ #
    from .models import DivergenceType
    all_divergences = []
    compared_words = 0
    for pi in range(n_pages):
        groups, unm_emb, unm_ocr = match_words(page_embedded[pi], page_ocr[pi], cfg)
        divs = classify_page(groups, unm_emb, unm_ocr, cfg)
        all_divergences.extend(divs)
        compared_words += len(groups) + len(unm_emb) + len(unm_ocr)

        diag["matched"] += len(groups)
        for d in divs:
            if d.type is DivergenceType.AGREE:
                diag["agree"] += 1
            elif d.type is DivergenceType.MISMATCH:
                diag["mismatch"] += 1

    # ------------------------------------------------------------------ #
    # Step 9 — score                                                     #
    # ------------------------------------------------------------------ #
    result = score(
        all_divergences, routed_to=None, config=cfg, compared_words=compared_words
    )

    try:
        prov = ocr_engine.provenance()
    except Exception:
        prov = None

    return OCRCrossCheckReport(
        path=path,
        ok=True,
        result=result,
        provenance=prov,
        diagnostics=dict(diag),
        notes=(),
        page_dims_px=tuple(page_dims_px_list),
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _rasterize_direct(pdf_bytes: bytes, dpi: int) -> list[bytes]:
    """Rasterise without a shared context (fallback for standalone analyze_path)."""
    try:
        import pypdfium2 as pdfium
    except Exception:
        return []
    try:
        scale = dpi / 72.0
        doc = pdfium.PdfDocument(pdf_bytes)
        images: list[bytes] = []
        try:
            for page in doc:
                bitmap = page.render(scale=scale)
                pil_image = bitmap.to_pil()
                buf = BytesIO()
                pil_image.save(buf, format="PNG")
                images.append(buf.getvalue())
        finally:
            doc.close()
        return images
    except Exception:
        return []


def _extract_layouts(pdf_bytes: bytes) -> list:
    """Extract pdfminer page layouts without a shared context."""
    try:
        from pdfminer.high_level import extract_pages
        return list(extract_pages(BytesIO(pdf_bytes)))
    except Exception:
        return []


def _get_rotations(pdf_bytes: bytes, ctx: object | None) -> list[int]:
    """Return page rotation values (0/90/180/270) from pikepdf."""
    try:
        pike = None
        if ctx is not None:
            try:
                pike = ctx.pikepdf_doc  # type: ignore[union-attr]
            except Exception:
                pass
        if pike is None:
            import pikepdf
            pike = pikepdf.open(BytesIO(pdf_bytes))
        rotations: list[int] = []
        for page in pike.pages:
            raw = page.get("/Rotate", None)
            if raw is None:
                rotations.append(0)
            else:
                try:
                    rotations.append(int(raw) % 360)
                except Exception:
                    rotations.append(0)
        return rotations
    except Exception:
        return []


def _page_px_dims(page_layout: object, scale: float, rotate: int) -> tuple[float, float]:
    """Return (width_px, height_px) for a rendered page in pixel space."""
    try:
        w_pt = float(page_layout.width)  # type: ignore[union-attr]
        h_pt = float(page_layout.height)  # type: ignore[union-attr]
    except Exception:
        return (595.0 * scale, 842.0 * scale)

    if rotate in (90, 270):
        return (h_pt * scale, w_pt * scale)
    return (w_pt * scale, h_pt * scale)


__all__ = ["analyze_bytes", "analyze_path"]
