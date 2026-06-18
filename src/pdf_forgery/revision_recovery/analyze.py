"""Stage 1 orchestration: raw bytes / file path -> :class:`AnalysisReport`.

Ties the pipeline together in one place so both the CLI and tests can request a
full analysis with a single call::

    detect -> reconstruct -> (per consecutive pair) diff_text + diff_objects
           -> score -> build findings -> AnalysisReport

The function is read-only (it never writes the input) and never raises: a file
that cannot be read becomes an ``AnalysisReport`` with ``ok=False`` and an
``error`` message, mirroring the "report and continue, never crash" constraint.
Rendering to JSON / human summary lives in ``report.py``.
"""

from __future__ import annotations

from pathlib import Path

from ..core.types import StageResult
from .adapter import report_to_stage_result
from .config import Config
from .detect import detect
from .diff.objectdiff import diff_objects
from .diff.textdiff import diff_normalized_pages, diff_text
from .extract.glyph_text import glyph_page_texts, looks_incomplete
from .extract.locate import locate_findings
from .extract.normalize import normalize
from .extract.text import extract_text_per_page
from .models import (
    AnalysisReport,
    Finding,
    ObjectChange,
    ObjectChangeClass,
    ObjectDiff,
    Revision,
    TextChange,
)
from .reconstruct import reconstruct
from .scoring import score


# ---------------------------------------------------------------------------
# Finding construction
# ---------------------------------------------------------------------------

def _objgen_str(change: ObjectChange) -> str:
    """Render an object change's id as ``"<obj> <gen>"``."""
    return f"{change.obj_num} {change.gen_num}"


def _content_changes_for_page(
    object_diff: ObjectDiff,
    page_index: int | None,
) -> list[ObjectChange]:
    """CONTENT object changes for a page (or all CONTENT changes if page is None)."""
    result: list[ObjectChange] = []
    for ch in object_diff.changes:
        if ch.change_class is not ObjectChangeClass.CONTENT:
            continue
        if page_index is None or ch.page_index is None or ch.page_index == page_index:
            result.append(ch)
    return result


def _build_findings(
    text_changes: list[TextChange],
    object_diffs: list[ObjectDiff],
) -> list[Finding]:
    """Assemble per-change findings from the text and object diffs.

    Primary findings come from substantive page-level text changes, enriched
    with the CONTENT object id(s) for the same revision pair and page.  Object
    changes that carry no text evidence (OVERLAY, FIELD_EDIT, FORM_FILL) are also
    surfaced as findings so the reviewer sees every flagged change, not only the
    text edits.
    """
    findings: list[Finding] = []

    # Index object diffs by revision pair for cross-referencing.
    od_by_pair: dict[tuple[int, int], ObjectDiff] = {
        (od.from_revision, od.to_revision): od for od in object_diffs
    }

    text_evidence_pairs: set[tuple[int, int]] = set()

    for tc in text_changes:
        pair = (tc.from_revision, tc.to_revision)
        od = od_by_pair.get(pair)
        for pd in tc.page_diffs:
            if not pd.is_substantive:
                continue
            text_evidence_pairs.add(pair)
            content_changes = (
                _content_changes_for_page(od, pd.page_index) if od else []
            )
            hv_kind = next(
                (t.high_value for t in pd.token_changes if t.high_value is not None),
                None,
            )
            n_tokens = len(pd.token_changes)
            noun = "token" if n_tokens == 1 else "tokens"
            hv_tag = f" (high-value: {hv_kind.value})" if hv_kind else ""
            findings.append(
                Finding(
                    from_revision=tc.from_revision,
                    to_revision=tc.to_revision,
                    page_index=pd.page_index,
                    object_ids=tuple(_objgen_str(c) for c in content_changes),
                    object_classes=tuple(c.change_class for c in content_changes),
                    token_changes=pd.token_changes,
                    is_high_value=pd.has_high_value_change,
                    high_value_kind=hv_kind,
                    summary=(
                        f"text edited on page {pd.page_index + 1}: "
                        f"{n_tokens} {noun} changed{hv_tag}"
                    ),
                )
            )

    # Non-text object changes worth flagging on their own (no text evidence).
    flaggable = {
        ObjectChangeClass.OVERLAY: "overlay added (stamp/redaction/covering annotation)",
        ObjectChangeClass.FIELD_EDIT: "form field value changed from a prior value",
        ObjectChangeClass.FORM_FILL: "form field filled for the first time",
    }
    for od in object_diffs:
        pair = (od.from_revision, od.to_revision)
        for ch in od.changes:
            desc = flaggable.get(ch.change_class)
            if desc is None:
                continue
            # CONTENT-with-no-text already surfaced below; here only the
            # object-only classes above.
            page_txt = (
                f" on page {ch.page_index + 1}" if ch.page_index is not None else ""
            )
            findings.append(
                Finding(
                    from_revision=od.from_revision,
                    to_revision=od.to_revision,
                    page_index=ch.page_index,
                    object_ids=(_objgen_str(ch),),
                    object_classes=(ch.change_class,),
                    token_changes=(),
                    is_high_value=False,
                    high_value_kind=None,
                    summary=f"{desc}{page_txt}",
                )
            )

        # CONTENT changed but the text layer shows nothing — possible overlay /
        # inpainting; flag it so it is never silently dropped.
        if pair not in text_evidence_pairs:
            for ch in _content_changes_for_page(od, None):
                page_txt = (
                    f" on page {ch.page_index + 1}"
                    if ch.page_index is not None
                    else ""
                )
                findings.append(
                    Finding(
                        from_revision=od.from_revision,
                        to_revision=od.to_revision,
                        page_index=ch.page_index,
                        object_ids=(_objgen_str(ch),),
                        object_classes=(ch.change_class,),
                        token_changes=(),
                        is_high_value=False,
                        high_value_kind=None,
                        summary=(
                            f"content stream changed but text layer is unchanged"
                            f"{page_txt} (possible overlay; OCR cross-check recommended)"
                        ),
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# Glyph-based fallback text diff (FIX 2)
# ---------------------------------------------------------------------------

def _diff_text_with_fallback(
    rev_a: Revision,
    rev_b: Revision,
    cfg: Config,
    notes: list[str],
) -> TextChange:
    """Primary text diff, with a glyph-based fallback for incomplete extraction.

    The primary container-level extractor stays authoritative. Only when it
    found NO substantive change *and* its output looks suspiciously incomplete
    for a revision (far fewer characters, or missing high-value tokens, than the
    shared glyph extractor recovers) do we re-diff the revision pair from grouped
    glyphs. The fallback result REPLACES the primary one only when it touches a
    high-value token (amount/date/ID) — escalating the eventual MEDIUM to HIGH;
    a non-high-value prose-only fallback diff is left advisory (primary kept), so
    files where primary extraction already works are never affected.
    """
    primary = diff_text(rev_a, rev_b, cfg)
    if not cfg.enable_glyph_fallback:
        return primary
    if primary.is_substantive:
        return primary  # primary already works -> never override (regression guard)

    norm_a = [normalize(p, cfg) for p in extract_text_per_page(rev_a.data)]
    norm_b = [normalize(p, cfg) for p in extract_text_per_page(rev_b.data)]
    if not (
        looks_incomplete(norm_a, rev_a.data, cfg)
        or looks_incomplete(norm_b, rev_b.data, cfg)
    ):
        return primary

    glyph_a = [normalize(p, cfg) for p in glyph_page_texts(rev_a.data)]
    glyph_b = [normalize(p, cfg) for p in glyph_page_texts(rev_b.data)]
    fb = diff_normalized_pages(glyph_a, glyph_b, rev_a.index, rev_b.index)

    if fb.is_substantive and fb.has_high_value_change:
        note = (
            f"primary text extraction was incomplete for revisions "
            f"{rev_a.index}->{rev_b.index}; glyph-based fallback recovered a "
            f"high-value text change the primary extractor missed"
        )
        # Carried on the returned TextChange's notes (merged by analyze_bytes);
        # not also appended to `notes` here, to avoid a duplicate entry.
        return TextChange(
            from_revision=fb.from_revision,
            to_revision=fb.to_revision,
            page_diffs=fb.page_diffs,
            is_substantive=fb.is_substantive,
            has_high_value_change=fb.has_high_value_change,
            notes=fb.notes + (note,),
        )

    if fb.is_substantive:
        # Fallback saw only prose changes: stay advisory, keep the primary result.
        notes.append(
            f"glyph-based fallback found a non-high-value text change for "
            f"revisions {rev_a.index}->{rev_b.index}; left advisory (not escalated)"
        )
    return primary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_bytes(
    raw: bytes,
    path: str = "<bytes>",
    config: Config | None = None,
) -> AnalysisReport:
    """Run the full Stage 1 pipeline over raw PDF bytes.

    Never raises: any per-stage failure is captured in ``notes`` / failure
    objects and reflected in the scoring tier.
    """
    cfg = config or Config()

    detection = detect(raw)
    recon = reconstruct(raw, detection)
    revisions: tuple[Revision, ...] = recon.revisions

    text_changes: list[TextChange] = []
    object_diffs: list[ObjectDiff] = []
    fallback_notes: list[str] = []
    for rev_a, rev_b in zip(revisions, revisions[1:]):
        text_changes.append(_diff_text_with_fallback(rev_a, rev_b, cfg, fallback_notes))
        object_diffs.append(diff_objects(rev_a, rev_b))

    scoring = score(text_changes, object_diffs, recon, cfg)
    findings = _build_findings(text_changes, object_diffs)

    # Localise added / changed text to page bounding boxes (advisory geometry;
    # never affects the tier/score). Diagnostics are collected in locate_notes.
    locate_notes: list[str] = []
    findings = locate_findings(findings, recon, cfg, locate_notes)

    notes = detection.notes + recon.notes + tuple(fallback_notes) + tuple(locate_notes)
    for tc in text_changes:
        notes += tc.notes
    for od in object_diffs:
        notes += od.notes

    return AnalysisReport(
        path=path,
        ok=True,
        error=None,
        raw_size=detection.raw_size,
        candidate_count=detection.candidate_count,
        revision_count=recon.revision_count,
        reconstruction_failures=len(recon.failures),
        scoring=scoring,
        findings=tuple(findings),
        text_changes=tuple(text_changes),
        object_diffs=tuple(object_diffs),
        notes=notes,
    )


def analyze_path(
    path: str | Path,
    config: Config | None = None,
) -> AnalysisReport:
    """Read a PDF file (read-only) and run the full Stage 1 pipeline.

    A missing / unreadable / non-file path yields an ``AnalysisReport`` with
    ``ok=False`` and an ``error`` message rather than raising.
    """
    p = Path(path)
    try:
        if not p.exists():
            return _failed(str(path), "file not found")
        if p.is_dir():
            return _failed(str(path), "path is a directory, not a PDF file")
        raw = p.read_bytes()
    except OSError as exc:
        return _failed(str(path), f"could not read file: {exc}")

    return analyze_bytes(raw, str(path), config)


# ---------------------------------------------------------------------------
# Stage-schema entry points (core.types.StageResult)
# ---------------------------------------------------------------------------

def analyze_bytes_as_stage(
    raw: bytes,
    path: str = "<bytes>",
    config: Config | None = None,
) -> StageResult:
    """Run the full Stage 1 pipeline and return a core :class:`StageResult`.

    Identical detection / scoring to :func:`analyze_bytes`; the rich
    :class:`AnalysisReport` is mapped onto the shared stage schema (and preserved
    as the result's ``payload`` for the original renderers).
    """
    return report_to_stage_result(analyze_bytes(raw, path, config))


def analyze_path_as_stage(
    path: str | Path,
    config: Config | None = None,
) -> StageResult:
    """Read a PDF (read-only) and return a core :class:`StageResult`.

    Stage-schema counterpart of :func:`analyze_path`; never raises.
    """
    return report_to_stage_result(analyze_path(path, config))


def _failed(path: str, error: str) -> AnalysisReport:
    """Build an ``ok=False`` report for a run that never produced a result."""
    return AnalysisReport(
        path=path,
        ok=False,
        error=error,
        raw_size=0,
        candidate_count=0,
        revision_count=0,
        reconstruction_failures=0,
        scoring=None,
        findings=(),
        text_changes=(),
        object_diffs=(),
        notes=(error,),
    )
