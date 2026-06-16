"""Adapter between Stage 3's rich report and the core stage schema.

Mirrors every other stage's adapter: maps an :class:`OCRCrossCheckReport` onto a
core :class:`StageResult` (carrying the rich report as ``payload`` so the
original JSON / human summary renderers keep working), and back. ``STAGE_NAME``
is the stable identifier stamped onto findings and used by fusion.

Stage 3 is a SUBSTANTIVE fusion stage (it can originate a verdict), so it is NOT
added to ``FusionConfig.corroborator_stages``.
"""

from __future__ import annotations

import json

from ..core.types import ConfidenceTier, Evidence, Finding, StageResult
from .models import Divergence, DivergenceType, OCRCrossCheckReport, TokenClass

STAGE_NAME = "ocr_crosscheck"

_HIGH_VALUE_LABEL: dict[TokenClass, str] = {
    TokenClass.AMOUNT: "amount",
    TokenClass.DATE: "date",
    TokenClass.ID: "id",
}


def _divergence_to_finding(d: Divergence) -> Finding | None:
    """Map one :class:`Divergence` to a core :class:`Finding`.

    AGREE divergences are skipped (they are not findings).
    """
    if d.type is DivergenceType.AGREE:
        return None

    emb_text = " ".join(w.text for w in d.embedded) if d.embedded else ""
    ocr_text = d.ocr.text if d.ocr is not None else ""

    if d.type is DivergenceType.MISMATCH:
        reason = (
            f"OCR render diverges from embedded text "
            f"(class: {d.token_class.value}, weight: {d.weight:.2f})"
        )
        before = emb_text
        after = ocr_text
        high_value = _HIGH_VALUE_LABEL.get(d.token_class)
        tier = (
            ConfidenceTier.HIGH
            if d.token_class in (TokenClass.AMOUNT, TokenClass.DATE, TokenClass.ID)
            else ConfidenceTier.MEDIUM
        )
    elif d.type is DivergenceType.EMBEDDED_ONLY:
        reason = (
            f"embedded word has no rendered counterpart — possible hidden text "
            f"(class: {d.token_class.value})"
        )
        before = emb_text
        after = ""
        high_value = _HIGH_VALUE_LABEL.get(d.token_class)
        tier = (
            ConfidenceTier.HIGH
            if d.token_class in (TokenClass.AMOUNT, TokenClass.DATE, TokenClass.ID)
            else ConfidenceTier.MEDIUM
        )
    else:  # OCR_ONLY
        reason = (
            f"OCR sees word with no embedded counterpart — possible image overlay "
            f"(class: {d.token_class.value})"
        )
        before = ""
        after = ocr_text
        high_value = _HIGH_VALUE_LABEL.get(d.token_class)
        tier = (
            ConfidenceTier.HIGH
            if d.token_class in (TokenClass.AMOUNT, TokenClass.DATE, TokenClass.ID)
            else ConfidenceTier.MEDIUM
        )

    evidence: list[Evidence] = []
    if before:
        evidence.append(Evidence(label="embedded", before=before, after=""))
    if after:
        evidence.append(Evidence(label="ocr", before="", after=after))

    return Finding(
        stage=STAGE_NAME,
        tier=tier,
        reason=reason,
        page=d.page_index,
        object_ids=(),
        before=before or None,
        after=after or None,
        high_value=high_value,
        evidence=tuple(evidence),
    )


def report_to_stage_result(report: OCRCrossCheckReport) -> StageResult:
    """Convert an :class:`OCRCrossCheckReport` into a core :class:`StageResult`."""
    if not report.ok:
        return StageResult(
            stage=STAGE_NAME,
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            findings=(),
            summary=f"{STAGE_NAME}: could not analyse file ({report.error})",
            reasons=(),
            notes=report.notes,
            ok=False,
            error=report.error,
            payload=report,
        )

    result = report.result
    if result is None:
        return StageResult(
            stage=STAGE_NAME,
            tier=ConfidenceTier.INCONCLUSIVE,
            score=None,
            findings=(),
            summary=f"{STAGE_NAME}: no result produced",
            ok=True,
            payload=report,
        )

    findings = tuple(
        f for d in result.divergences
        if (f := _divergence_to_finding(d)) is not None
    )

    notes = list(report.notes)
    if result.routed_to:
        notes.append(f"routed to {result.routed_to} (scanned/text-sparse)")

    n_findings = len(findings)
    score_txt = "n/a" if result.score is None else str(result.score)
    if result.tier is ConfidenceTier.INCONCLUSIVE:
        summary = f"{STAGE_NAME}: inconclusive"
        if result.routed_to:
            summary += f" (routed to {result.routed_to})"
        elif not result.divergences:
            summary += " (no words compared)"
    else:
        noun = "finding" if n_findings == 1 else "findings"
        summary = (
            f"{STAGE_NAME}: {result.tier.value.upper()} (score {score_txt}); "
            f"{n_findings} {noun}"
        )

    n_divergent = sum(
        1 for d in result.divergences if d.type is not DivergenceType.AGREE
    )
    reasons: list[str] = []
    if n_divergent:
        reasons.append(f"{n_divergent} divergent comparison(s) found")
    if result.routed_to:
        reasons.append(f"text-sparse — routed to {result.routed_to}")

    return StageResult(
        stage=STAGE_NAME,
        tier=result.tier,
        score=result.score,
        findings=findings,
        summary=summary,
        reasons=tuple(reasons),
        notes=tuple(notes),
        ok=True,
        error=None,
        payload=report,
    )


def stage_result_to_report(result: StageResult) -> OCRCrossCheckReport:
    """Recover the rich :class:`OCRCrossCheckReport` carried by a stage result."""
    payload = result.payload
    if not isinstance(payload, OCRCrossCheckReport):
        raise TypeError(
            f"stage result payload is {type(payload).__name__!r}, "
            f"expected OCRCrossCheckReport"
        )
    return payload


def render_stage_json(result: StageResult, *, indent: int = 2) -> str:
    """Render a Stage-3 :class:`StageResult` as JSON (PHI-safe; advisory)."""
    report = stage_result_to_report(result)

    data: dict = {
        "stage": STAGE_NAME,
        "path": report.path,
        "ok": report.ok,
        "tier": result.tier.value,
        "score": result.score,
        "error": report.error,
        "notes": list(report.notes),
        "diagnostics": report.diagnostics,
    }

    if report.provenance:
        prov = report.provenance
        data["provenance"] = {
            "engine": prov.engine,
            "model_version": prov.model_version,
            "language": prov.language,
            "device": prov.device,
            "render_dpi": prov.render_dpi,
        }

    if report.result:
        r = report.result
        data["routed_to"] = r.routed_to
        data["divergence_count"] = len(r.divergences)
        data["divergence_mass"] = round(
            sum(d.weight for d in r.divergences if d.type is not DivergenceType.AGREE),
            4,
        )
        # PHI-safe finding list (no raw text in log — only counts + classes).
        findings_summary = []
        for d in r.divergences:
            if d.type is not DivergenceType.AGREE:
                findings_summary.append({
                    "type": d.type.value,
                    "token_class": d.token_class.value,
                    "weight": d.weight,
                    "page": d.page_index,
                })
        data["divergences"] = findings_summary

    return json.dumps(data, indent=indent, default=str)


def render_stage_summary(result: StageResult) -> str:
    """Render a Stage-3 :class:`StageResult` as the human before→after summary."""
    report = stage_result_to_report(result)
    lines: list[str] = [
        f"=== OCR Cross-Check (Stage 3) ===",
        f"File   : {report.path}",
        f"Tier   : {result.tier.value.upper()}",
        f"Score  : {result.score if result.score is not None else 'n/a'}",
    ]

    if report.provenance:
        p = report.provenance
        lines.append(
            f"Engine : {p.engine} {p.model_version}  lang={p.language}  "
            f"device={p.device}  dpi={p.render_dpi}"
        )

    if report.result and report.result.routed_to:
        lines.append(f"Routed : → {report.result.routed_to}")

    for note in report.notes:
        lines.append(f"Note   : {note}")

    if report.error:
        lines.append(f"Error  : {report.error}")
        return "\n".join(lines)

    if report.result:
        r = report.result
        agree = sum(1 for d in r.divergences if d.type is DivergenceType.AGREE)
        divergent = len(r.divergences) - agree
        lines.append(f"Compared: {len(r.divergences)} groups  ({agree} agree, {divergent} divergent)")

        for d in r.divergences:
            if d.type is DivergenceType.AGREE:
                continue
            emb = " ".join(w.text for w in d.embedded) if d.embedded else "(none)"
            ocr = d.ocr.text if d.ocr else "(none)"
            lines.append(
                f"  [{d.type.value:<15}] p{d.page_index}  class={d.token_class.value}  "
                f"w={d.weight:.2f}  embedded={emb!r}  ocr={ocr!r}"
            )

    return "\n".join(lines)


__all__ = [
    "STAGE_NAME",
    "report_to_stage_result",
    "stage_result_to_report",
    "render_stage_json",
    "render_stage_summary",
]
