"""Orchestration: glyph extraction -> detection -> scoring -> FontReport.

``analyze_bytes`` / ``analyze_path`` return the rich :class:`FontReport`;
``analyze_bytes_as_stage`` / ``analyze_path_as_stage`` return a core
:class:`~pdf_forgery.core.types.StageResult` via the adapter. None of these ever
raise — a processing failure becomes an ``ok=False`` report.

When a shared :class:`~pdf_forgery.core.context.AnalysisContext` is available the
stage consumes its cached ``page_layouts`` so the file is parsed once across all
stages; otherwise it extracts glyphs straight from the bytes.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from ..core.context import AnalysisContext
from ..core.types import StageResult
from .config import FontConfig
from .detect import detect_findings
from .extract import distinct_fonts, glyphs_from_bytes, glyphs_from_layouts
from .models import ConfidenceTier, FontReport, Glyph
from .scoring import score_findings


def analyze_bytes(
    raw: bytes,
    path: str = "<bytes>",
    config: FontConfig | None = None,
    *,
    ctx: AnalysisContext | None = None,
) -> FontReport:
    """Analyse PDF *raw* bytes and return a :class:`FontReport` (never raises)."""
    cfg = config or FontConfig()
    notes: list[str] = []

    try:
        glyphs, page_count, page_dims, page_rotations = _extract(raw, ctx, notes, cfg)
    except Exception as exc:  # defensive: extraction must never abort the stage
        return FontReport(
            path=path,
            ok=False,
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            error=f"font extraction failed: {exc}",
            raw_size=len(raw),
        )

    fonts = distinct_fonts(glyphs)
    if not glyphs:
        notes.append("no extractable text glyphs (image-only, empty, or encrypted)")

    findings, lines = detect_findings(glyphs, cfg)
    comparable = sum(1 for g in glyphs if not g.is_space)
    tier, score, reasons = score_findings(findings, len(fonts), comparable, cfg)

    return FontReport(
        path=path,
        ok=True,
        tier=tier,
        score=score,
        page_count=page_count,
        distinct_fonts=fonts,
        findings=tuple(findings),
        reasons=tuple(reasons),
        notes=tuple(notes),
        error=None,
        raw_size=len(raw),
        page_dims=page_dims,
        page_rotations=page_rotations,
        _lines=tuple(lines),
    )


def analyze_path(path: str | Path, config: FontConfig | None = None) -> FontReport:
    """Read a PDF file (read-only) and analyse it; missing/dir -> ok=False."""
    p = Path(path)
    try:
        if not p.exists():
            return _failed_report(str(path), "file not found")
        if p.is_dir():
            return _failed_report(str(path), "path is a directory, not a PDF file")
        raw = p.read_bytes()
    except OSError as exc:
        return _failed_report(str(path), f"could not read file: {exc}")
    return analyze_bytes(raw, str(path), config)


# ---------------------------------------------------------------------------
# Stage-schema variants
# ---------------------------------------------------------------------------

def analyze_bytes_as_stage(
    raw: bytes,
    path: str = "<bytes>",
    config: FontConfig | None = None,
    *,
    ctx: AnalysisContext | None = None,
) -> StageResult:
    """Analyse bytes and return a core :class:`StageResult`."""
    from .adapter import report_to_stage_result

    return report_to_stage_result(analyze_bytes(raw, path, config, ctx=ctx))


def analyze_path_as_stage(
    path: str | Path, config: FontConfig | None = None
) -> StageResult:
    """Analyse a file and return a core :class:`StageResult`."""
    from .adapter import report_to_stage_result

    return report_to_stage_result(analyze_path(path, config))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _extract(
    raw: bytes,
    ctx: AnalysisContext | None,
    notes: list[str],
    cfg: FontConfig,
) -> tuple[list[Glyph], int, tuple[tuple[float, float], ...], tuple[int, ...]]:
    """Extract glyphs plus per-page dims/rotations (preferring the shared context)."""
    if ctx is not None:
        layouts = ctx.page_layouts
        if layouts:
            if cfg.enable_localization:
                dims: tuple[tuple[float, float], ...] = tuple(
                    (float(p.width), float(p.height)) for p in layouts
                )
                rots: tuple[int, ...] = tuple(_get_page_rotations(raw, ctx))
            else:
                dims, rots = (), ()
            return glyphs_from_layouts(layouts), len(layouts), dims, rots
        # ctx present but no layouts (e.g. encrypted): fall through to bytes.
    glyphs = glyphs_from_bytes(raw)
    page_count = 1 + max((g.page_index for g in glyphs), default=-1)
    if cfg.enable_localization:
        dims = _get_page_dims_from_bytes(raw)
        rots = tuple(_get_page_rotations(raw, None))
    else:
        dims, rots = (), ()
    return glyphs, page_count, dims, rots


def _get_page_dims_from_bytes(raw: bytes) -> tuple[tuple[float, float], ...]:
    """Extract per-page (width_pt, height_pt) via pdfminer, falling back to pikepdf."""
    try:
        from pdfminer.high_level import extract_pages
        layouts = list(extract_pages(BytesIO(raw)))
        return tuple((float(p.width), float(p.height)) for p in layouts)
    except Exception:
        pass
    try:
        import pikepdf
        doc = pikepdf.open(BytesIO(raw))
        dims: list[tuple[float, float]] = []
        for page in doc.pages:
            mb = page.get("/MediaBox", None)
            if mb is not None:
                w = abs(float(mb[2]) - float(mb[0]))
                h = abs(float(mb[3]) - float(mb[1]))
                dims.append((w, h))
            else:
                dims.append((595.0, 842.0))
        return tuple(dims)
    except Exception:
        return ()


def _get_page_rotations(raw: bytes, ctx: object | None) -> list[int]:
    """Return per-page /Rotate values (0/90/180/270) from pikepdf."""
    try:
        pike = None
        if ctx is not None:
            try:
                pike = ctx.pikepdf_doc  # type: ignore[union-attr]
            except Exception:
                pass
        if pike is None:
            import pikepdf
            pike = pikepdf.open(BytesIO(raw))
        rotations: list[int] = []
        for page in pike.pages:
            raw_r = page.get("/Rotate", None)
            if raw_r is None:
                rotations.append(0)
            else:
                try:
                    rotations.append(int(raw_r) % 360)
                except Exception:
                    rotations.append(0)
        return rotations
    except Exception:
        return []


def _failed_report(path: str, error: str) -> FontReport:
    return FontReport(
        path=path,
        ok=False,
        tier=ConfidenceTier.INCONCLUSIVE,
        score=None,
        error=error,
        notes=(error,),
    )
