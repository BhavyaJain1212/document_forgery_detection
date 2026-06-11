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

from .config import Config
from .detect import detect
from .diff.objectdiff import diff_objects
from .diff.textdiff import diff_text
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
    for rev_a, rev_b in zip(revisions, revisions[1:]):
        text_changes.append(diff_text(rev_a, rev_b, cfg))
        object_diffs.append(diff_objects(rev_a, rev_b))

    scoring = score(text_changes, object_diffs, recon, cfg)
    findings = _build_findings(text_changes, object_diffs)

    notes = detection.notes + recon.notes
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
