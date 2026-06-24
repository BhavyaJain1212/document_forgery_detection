"""Shared, lazily-cached analysis artifacts for one PDF.

A :class:`AnalysisContext` holds the raw bytes ONCE and parses expensive shared
artifacts (the pikepdf document, pdfminer page layouts, rasterized page images)
on first access, caching the result. Every stage in a pipeline run receives the
same context, so the file is parsed at most once per artifact across all stages
instead of each stage re-reading and re-parsing it.

All artifact accessors are tolerant: a malformed / encrypted / unsupported input
yields an empty result (``None`` / ``[]``) rather than raising, matching the
"report and continue, never crash" constraint. Rasterization is optional — if no
rendering backend is installed it simply returns ``[]``.

The context is READ-ONLY with respect to the input bytes; nothing here mutates
``pdf_bytes``.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pikepdf
    from pdfminer.layout import LTPage


class AnalysisContext:
    """Raw PDF bytes plus lazily-parsed, cached shared artifacts."""

    def __init__(self, pdf_bytes: bytes, path: str | None = None) -> None:
        self._pdf_bytes = pdf_bytes
        self._path = path
        # Caches. The sentinel ``_UNSET`` distinguishes "not computed yet" from a
        # legitimately cached ``None`` (e.g. a doc that failed to open).
        self._pike: Any = _UNSET
        self._layouts: list["LTPage"] | None = None
        self._raster_cache: dict[int, list[bytes]] = {}
        # Generic, stage-scoped cache. A stage stashes a derived artifact under
        # its own namespaced key (e.g. ``"<stage>.decoded"``) so an
        # expensive once-per-file computation is shared across calls within a
        # run, the same way the typed caches above are. Never holds input bytes.
        self._stage_cache: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Inputs
    # ------------------------------------------------------------------ #
    @property
    def pdf_bytes(self) -> bytes:
        """The raw input bytes (never mutated)."""
        return self._pdf_bytes

    @property
    def path(self) -> str | None:
        """Originating filesystem path, if the context was built from a file."""
        return self._path

    @property
    def stage_cache(self) -> dict[str, Any]:
        """Generic stage-scoped cache (namespaced keys), shared for one run.

        Lets a stage memoise a derived artifact on the shared context instead of
        recomputing it on each call. Holds derived data only — never the raw
        input bytes. Cleared implicitly when the context is discarded.
        """
        return self._stage_cache

    # ------------------------------------------------------------------ #
    # Shared artifact: pikepdf document
    # ------------------------------------------------------------------ #
    @property
    def pikepdf_doc(self) -> "pikepdf.Pdf | None":
        """The opened pikepdf document, or ``None`` if it could not be opened.

        Cached after the first access. Opened with an empty password so
        empty-user-password encryption still loads; anything that genuinely
        cannot open yields ``None`` instead of raising.
        """
        if self._pike is _UNSET:
            self._pike = self._open_pike()
        return self._pike

    def _open_pike(self) -> "pikepdf.Pdf | None":
        try:
            import pikepdf

            return pikepdf.open(BytesIO(self._pdf_bytes))
        except Exception:  # corrupt / encrypted-needs-password / unsupported
            return None

    # ------------------------------------------------------------------ #
    # Shared artifact: pdfminer page layouts
    # ------------------------------------------------------------------ #
    @property
    def page_layouts(self) -> list["LTPage"]:
        """pdfminer ``LTPage`` layout objects, one per page (0-based order).

        Cached after the first access. On total failure returns ``[]``. Stages
        that only need the text layer can iterate these instead of re-running
        pdfminer themselves.
        """
        if self._layouts is None:
            self._layouts = self._extract_layouts()
        return self._layouts

    def _extract_layouts(self) -> list["LTPage"]:
        try:
            from pdfminer.high_level import extract_pages

            return list(extract_pages(BytesIO(self._pdf_bytes)))
        except Exception:  # malformed / encrypted / corrupt: never crash
            return []

    # ------------------------------------------------------------------ #
    # Shared artifact: rasterized page images (optional backend)
    # ------------------------------------------------------------------ #
    def rasterized_pages(self, dpi: int = 150) -> list[bytes]:
        """Rasterized page images as PNG bytes, one per page (0-based order).

        Used by later pixel-level stages (e.g. OCR cross-check). Cached per DPI.
        Rasterization requires an optional rendering backend (``pypdfium2``); if
        none is installed, or rendering fails, this returns ``[]`` so stages can
        degrade gracefully rather than crash.
        """
        if dpi not in self._raster_cache:
            self._raster_cache[dpi] = self._rasterize(dpi)
        return self._raster_cache[dpi]

    def _rasterize(self, dpi: int) -> list[bytes]:
        try:
            import pypdfium2 as pdfium  # optional, not a hard dependency
        except Exception:
            return []
        try:
            scale = dpi / 72.0
            doc = pdfium.PdfDocument(self._pdf_bytes)
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

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """Release the cached pikepdf document handle, if any."""
        if self._pike is not _UNSET and self._pike is not None:
            try:
                self._pike.close()
            except Exception:
                pass
        self._pike = _UNSET

    def __enter__(self) -> "AnalysisContext":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# Sentinel for "cache slot not yet computed".
_UNSET: Any = object()
