"""Render :class:`AnalysisReport` to machine JSON and a human-readable summary.

Two public renderers:

    report_to_dict(report)      -> a JSON-safe dict (one analysed file)
    render_json(reports, ...)   -> a JSON string (object for one file, array for
                                   a batch); machine-readable.
    render_summary(report)      -> a human-readable, before -> after summary.

Confidence is advisory throughout — the human summary states this explicitly.
The JSON is fully local, deterministic, and contains no file bytes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from .models import (
    AnalysisReport,
    CharSpan,
    Finding,
    ObjectDiff,
    ScoringResult,
    TextChange,
    TokenDiff,
)

# Confidence is advisory, never a verdict — surfaced in every human summary.
_ADVISORY = (
    "Confidence is ADVISORY. This tool flags evidence; a human reviewer makes "
    "the final call."
)


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------

def _char_span_to_dict(span: CharSpan) -> dict:
    return {"tag": span.tag, "before": span.before, "after": span.after}


def _token_diff_to_dict(td: TokenDiff) -> dict:
    return {
        "before": td.before,
        "after": td.after,
        "high_value": td.high_value.value if td.high_value else None,
        "char_diff": [_char_span_to_dict(s) for s in td.char_diff],
    }


def _finding_to_dict(f: Finding) -> dict:
    return {
        "from_revision": f.from_revision,
        "to_revision": f.to_revision,
        "page_index": f.page_index,
        "page_number": (f.page_index + 1) if f.page_index is not None else None,
        "object_ids": list(f.object_ids),
        "object_classes": [c.value for c in f.object_classes],
        "is_high_value": f.is_high_value,
        "high_value_kind": f.high_value_kind.value if f.high_value_kind else None,
        "before_text": f.before_text,
        "after_text": f.after_text,
        "summary": f.summary,
        "token_changes": [_token_diff_to_dict(t) for t in f.token_changes],
    }


def _scoring_to_dict(s: ScoringResult) -> dict:
    return {
        "tier": s.tier.value,
        "score": s.score,
        "reasons": list(s.reasons),
        "object_classes_seen": [c.value for c in s.object_classes_seen],
        "has_substantive_text_change": s.has_substantive_text_change,
        "has_high_value_change": s.has_high_value_change,
        "high_value_kind": s.high_value_kind.value if s.high_value_kind else None,
        "revision_count": s.revision_count,
        "has_reconstruction_failures": s.has_reconstruction_failures,
        "notes": list(s.notes),
    }


def _text_change_to_dict(tc: TextChange) -> dict:
    return {
        "from_revision": tc.from_revision,
        "to_revision": tc.to_revision,
        "is_substantive": tc.is_substantive,
        "has_high_value_change": tc.has_high_value_change,
        "notes": list(tc.notes),
        "page_diffs": [
            {
                "page_index": pd.page_index,
                "is_substantive": pd.is_substantive,
                "has_high_value_change": pd.has_high_value_change,
                "token_changes": [_token_diff_to_dict(t) for t in pd.token_changes],
            }
            for pd in tc.page_diffs
        ],
    }


def _object_diff_to_dict(od: ObjectDiff) -> dict:
    return {
        "from_revision": od.from_revision,
        "to_revision": od.to_revision,
        "notes": list(od.notes),
        "changes": [
            {
                "obj": ch.obj_num,
                "gen": ch.gen_num,
                "class": ch.change_class.value,
                "page_index": ch.page_index,
                "is_new": ch.is_new,
                "notes": list(ch.notes),
            }
            for ch in od.changes
        ],
    }


def report_to_dict(report: AnalysisReport) -> dict:
    """Convert one :class:`AnalysisReport` to a JSON-safe dict."""
    return {
        "path": report.path,
        "ok": report.ok,
        "error": report.error,
        "advisory": _ADVISORY,
        "raw_size": report.raw_size,
        "candidate_count": report.candidate_count,
        "revision_count": report.revision_count,
        "reconstruction_failures": report.reconstruction_failures,
        "scoring": _scoring_to_dict(report.scoring) if report.scoring else None,
        "findings": [_finding_to_dict(f) for f in report.findings],
        "text_changes": [_text_change_to_dict(tc) for tc in report.text_changes],
        "object_diffs": [_object_diff_to_dict(od) for od in report.object_diffs],
        "notes": list(report.notes),
    }


def render_json(
    reports: AnalysisReport | Sequence[AnalysisReport],
    *,
    indent: int = 2,
) -> str:
    """Render one report as a JSON object, or many as a JSON array.

    A single :class:`AnalysisReport` -> object; a sequence (batch) -> array,
    one entry per file, preserving order.
    """
    if isinstance(reports, AnalysisReport):
        payload: object = report_to_dict(reports)
    else:
        payload = [report_to_dict(r) for r in reports]
    return json.dumps(payload, indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Human-readable summary
# ---------------------------------------------------------------------------

def _truncate(text: str, limit: int = 200) -> str:
    """Trim long strings for readable terminal output."""
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _render_finding(f: Finding, n: int) -> list[str]:
    lines: list[str] = []
    lines.append(f"  [{n}] {f.summary}")
    lines.append(f"      revisions: {f.from_revision} -> {f.to_revision}")
    page = "n/a" if f.page_index is None else str(f.page_index + 1)
    lines.append(f"      page: {page}")
    if f.object_ids:
        ids = ", ".join(f.object_ids)
        classes = ", ".join(c.value for c in f.object_classes)
        lines.append(f"      object(s): {ids}  [{classes}]")
    elif f.object_classes:
        classes = ", ".join(c.value for c in f.object_classes)
        lines.append(f"      class: {classes}")
    if f.high_value_kind is not None:
        lines.append(f"      high-value: {f.high_value_kind.value}")
    if f.token_changes:
        before = _truncate(f.before_text) or "(nothing)"
        after = _truncate(f.after_text) or "(nothing)"
        lines.append(f"      before: {before}")
        lines.append(f"      after:  {after}")
    return lines


def render_summary(report: AnalysisReport) -> str:
    """Render a human-readable, before -> after summary of one analysis."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"FILE: {report.path}")
    lines.append("=" * 72)

    if not report.ok:
        lines.append(f"ERROR: {report.error}")
        lines.append("Could not analyse this file (reported, not fatal).")
        return "\n".join(lines)

    s = report.scoring
    tier = s.tier.value.upper() if s else "n/a"
    score = "n/a" if (s is None or s.score is None) else str(s.score)
    lines.append(f"CONFIDENCE: {tier}  (score: {score})")
    lines.append(_ADVISORY)
    lines.append("")
    lines.append(
        f"revisions reconstructed: {report.revision_count}  |  "
        f"candidate boundaries: {report.candidate_count}  |  "
        f"reconstruction failures: {report.reconstruction_failures}"
    )

    if s and s.reasons:
        lines.append("")
        lines.append("Why:")
        for r in s.reasons:
            lines.append(f"  - {r}")

    lines.append("")
    if report.findings:
        n_find = len(report.findings)
        noun = "finding" if n_find == 1 else "findings"
        lines.append(f"FLAGGED CHANGES ({n_find} {noun}):")
        for i, f in enumerate(report.findings, start=1):
            lines.extend(_render_finding(f, i))
            lines.append("")
    else:
        lines.append("FLAGGED CHANGES: none")
        if report.revision_count <= 1:
            lines.append(
                "  Single revision — inconclusive for this method; "
                "later stages (font / OCR) are needed."
            )

    if report.notes:
        lines.append("Notes:")
        for note in report.notes:
            lines.append(f"  - {note}")

    return "\n".join(lines).rstrip()
