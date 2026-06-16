"""Pluggable OCR engine boundary for Stage 3 (GPU queue).

The render + recognition step sits behind the :class:`OCREngine` protocol so the
backend is swappable without touching the CPU-side logic. Three implementations:

- :class:`PaddleOCREngine` — production default, GPU. Lazily imports PaddleOCR
  to avoid a hard import-time dependency; degrades gracefully when absent.
- :class:`TesseractEngine` — TODO stub; same interface, swap in for A/B testing.
- :class:`StubOCREngine` — deterministic fake, for unit tests.

The engine is the only GPU-bound step. It accepts page PNG bytes (rasterised by
the caller at ``dpi`` DPI via pypdfium2) and returns word-level
:class:`WordBox` objects in PIXEL space (top-left origin), each with the
engine's per-word confidence.

PHI note: the engine returns raw word text. Callers must never log ``WordBox.text``
directly — only counts / positions / hashes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .align import quad_to_bbox
from .config import OCRCrossCheckConfig
from .models import RenderProvenance, WordBox, WordSource


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class OCREngine(Protocol):
    """A pluggable OCR backend that recognises words from a rendered page."""

    name: str

    def recognize(self, page_png: bytes, *, dpi: int) -> list[WordBox]:
        """Recognise word boxes from one rendered page (PNG bytes).

        Returns pixel-space :class:`WordBox` objects (top-left origin) with
        ``source=WordSource.OCR`` and a per-word ``conf`` in ``[0, 1]``.
        Returns ``[]`` on failure — never raises.
        """
        ...

    def provenance(self) -> RenderProvenance:
        """Engine identity + model version + language + device + render DPI.

        Called once per analysis run; the result is embedded in the report for
        reproducibility.
        """
        ...


# ---------------------------------------------------------------------------
# PaddleOCR (GPU, default)
# ---------------------------------------------------------------------------

class PaddleOCREngine:
    """Default OCR engine — PaddleOCR 3.x on GPU.

    Wraps PaddleOCR's detection-driven recognition pipeline:
    1. Document-preprocessing (orientation classify / unwarp / textline
       orientation) is OFF by default — every page here is a digital-native PDF
       rasterised flat and upright by pypdfium2, and those models would warp the
       OCR coordinates out of the rendered raster's space (see
       ``OCRCrossCheckConfig.paddle_use_doc_unwarping`` and
       ``docs/STAGE3_OCR_FALSE_POSITIVE_FIX.md``).
    2. Language is configurable (default ``en``; set ``hi`` for Devanagari).
    3. The PaddleOCR object is created lazily on first :meth:`recognize` call so
       importing this module never fails when PaddleOCR is absent.

    PaddleOCR returns quadrilateral detection boxes; we reduce each to its
    axis-aligned bbox via :func:`~align.quad_to_bbox` before creating
    :class:`WordBox` objects.

    Output format verified against the installed PaddleOCR 3.7::

        result = ocr.predict(img_array)
        # result: list (one per image) of OCRResult (dict-like), each with
        # parallel arrays: rec_texts[i], rec_scores[i], rec_polys[i] (4x[x,y])
    """

    name = "paddleocr"

    def __init__(
        self,
        config: OCRCrossCheckConfig | None = None,
        *,
        language: str = "en",
        device: str = "cuda:0",
    ) -> None:
        self._config = config or OCRCrossCheckConfig()
        self._language = language
        self._device = device
        self._ocr: object | None = None        # lazy
        self._version: str = "unknown"
        self._model_version: str = "unknown"

    # ------------------------------------------------------------------ #
    # OCREngine protocol
    # ------------------------------------------------------------------ #

    def recognize(self, page_png: bytes, *, dpi: int) -> list[WordBox]:
        """Run PaddleOCR over a rendered page → pixel-space word boxes.

        Applies the OCR-confidence floor (``config.ocr_conf_floor``) before
        returning results — only words above the floor are returned.

        Returns ``[]`` when PaddleOCR is not installed, on recognition failure,
        or when no text is detected on the page.
        """
        ocr = self._get_ocr()
        if ocr is None:
            return []
        try:
            import numpy as np
            from PIL import Image
            from io import BytesIO

            img = np.array(Image.open(BytesIO(page_png)).convert("RGB"))
            raw = ocr.predict(img)
            return self._parse_result(raw, dpi)
        except Exception:
            return []

    def provenance(self) -> RenderProvenance:
        """Report engine identity for the reproducibility record."""
        return RenderProvenance(
            engine=self.name,
            model_version=self._model_version,
            language=self._language,
            device=self._device,
            render_dpi=self._config.render_dpi,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """True when PaddleOCR can be imported and the engine can be initialised."""
        return self._get_ocr() is not None

    def _get_ocr(self) -> object | None:
        """Lazily instantiate PaddleOCR; return None if unavailable."""
        if self._ocr is not None:
            return self._ocr
        try:
            import paddleocr  # optional dependency

            self._version = _paddle_version(paddleocr)
            self._model_version = self._version

            # PaddleOCR 3.x took a single `device=` string (e.g. "gpu:0",
            # "cpu") and dropped use_gpu/gpu_id/show_log entirely — passing
            # those now raises ValueError("Unknown argument: ...").
            device = self._device.replace("cuda", "gpu")
            kwargs: dict[str, object] = {
                "lang": self._language,
                "device": device,
                # PaddleOCR 3.x runs a document-preprocessing sub-pipeline
                # (orientation classify + UVDoc unwarp + textline orientation)
                # by default. Every page Stage 3 sees is a digital-native PDF
                # rasterised flat and upright by pypdfium2 — those models only
                # warp the OCR coordinates out of the rendered raster's space,
                # breaking the embedded<->pixel correspondence the whole stage
                # depends on. See docs/STAGE3_OCR_FALSE_POSITIVE_FIX.md.
                "use_doc_orientation_classify": self._config.paddle_use_doc_orientation_classify,
                "use_doc_unwarping": self._config.paddle_use_doc_unwarping,
                "use_textline_orientation": self._config.paddle_use_textline_orientation,
            }

            self._ocr = paddleocr.PaddleOCR(**kwargs)
            return self._ocr
        except Exception:
            return None

    def _parse_result(self, raw: object, dpi: int) -> list[WordBox]:
        """Parse PaddleOCR's result list → filtered list of WordBox.

        ``ocr.predict(img)`` returns a list (one per input image) of
        ``OCRResult`` (dict-like), each holding parallel arrays
        ``rec_texts[i]`` / ``rec_scores[i]`` / ``rec_polys[i]`` (4 ``[x, y]``
        points). We feed one image at a time, so ``raw`` has exactly one
        result.
        """
        if not raw:
            return []
        try:
            page_result = raw[0]
            texts = page_result["rec_texts"]
            scores = page_result["rec_scores"]
            polys = page_result["rec_polys"]
        except (IndexError, KeyError, TypeError):
            return []

        floor = self._config.ocr_conf_floor
        words: list[WordBox] = []
        for text, score, quad in zip(texts, scores, polys):
            try:
                conf = float(score)
            except (TypeError, ValueError):
                continue
            if not text or quad is None or conf < floor:
                continue
            bbox = quad_to_bbox(quad)
            words.append(
                WordBox(
                    text=str(text),
                    bbox=bbox,
                    source=WordSource.OCR,
                    conf=conf,
                    page_index=0,   # page_index set by caller in analyze.py
                )
            )
        return words


def _paddle_version(paddleocr_module: object) -> str:
    """Best-effort version string from the paddleocr package."""
    v = getattr(paddleocr_module, "__version__", None)
    if v:
        return str(v)
    try:
        import importlib.metadata
        return importlib.metadata.version("paddleocr")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Tesseract (TODO stub)
# ---------------------------------------------------------------------------

class TesseractEngine:
    """Tesseract-backed OCR engine — same interface as PaddleOCR for A/B testing.

    TODO (Stage 3.2): wire up pytesseract / tesserocr with word-level bbox output
    (``image_to_data`` with ``Output.DICT``).  Accuracy on dense document text is
    typically lower than PaddleOCR for this workload but provides an independent
    cross-check.
    """

    name = "tesseract"

    def __init__(self, config: OCRCrossCheckConfig | None = None, *, language: str = "eng") -> None:
        self._config = config or OCRCrossCheckConfig()
        self._language = language

    def recognize(self, page_png: bytes, *, dpi: int) -> list[WordBox]:
        raise NotImplementedError("TesseractEngine: implementation deferred to Stage 3.2")

    def provenance(self) -> RenderProvenance:
        raise NotImplementedError("TesseractEngine: implementation deferred to Stage 3.2")


# ---------------------------------------------------------------------------
# Stub (testing only)
# ---------------------------------------------------------------------------

class StubOCREngine:
    """Deterministic OCR stub for unit and integration tests.

    Takes a pre-configured list of :class:`WordBox` objects to return on each
    ``recognize`` call (one list per page, keyed by ``page_index``).  Tracks
    call count for assertions.  Never fails.

    Usage::

        engine = StubOCREngine(pages={0: [WordBox(...), ...]})
        words = engine.recognize(page_png, dpi=300)
    """

    name = "stub"

    def __init__(
        self,
        pages: dict[int, list[WordBox]] | None = None,
        config: OCRCrossCheckConfig | None = None,
    ) -> None:
        self._pages: dict[int, list[WordBox]] = pages or {}
        self._config = config or OCRCrossCheckConfig()
        self._call_count = 0
        self._current_page: int = 0

    def is_available(self) -> bool:
        """Always True — the stub is always available."""
        return True

    def set_page(self, page_index: int) -> "StubOCREngine":
        """Set which page's word list to return on the next ``recognize`` call."""
        self._current_page = page_index
        return self

    def recognize(self, page_png: bytes, *, dpi: int) -> list[WordBox]:
        self._call_count += 1
        return list(self._pages.get(self._current_page, []))

    def provenance(self) -> RenderProvenance:
        return RenderProvenance(
            engine=self.name,
            model_version="stub-1.0",
            language="en",
            device="cpu",
            render_dpi=self._config.render_dpi,
        )

    @property
    def call_count(self) -> int:
        return self._call_count


__all__ = ["OCREngine", "PaddleOCREngine", "TesseractEngine", "StubOCREngine"]
