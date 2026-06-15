"""Run the complete PDF forgery-detection system against one PDF.

Quick use:

1. Change ``PDF_NAME`` below.
2. Run ``.venv/bin/python test.py``.

You may also override the configured file without editing this script:

    .venv/bin/python test.py "test_pdf's/tampered.pdf"
    .venv/bin/python test.py /absolute/path/to/invoice.pdf

The script is read-only. It runs every detector with one shared analysis
context, prints each stage's findings, and then prints the fused assessment.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pdf_forgery.font_forensics import FontForensicsStage
from pdf_forgery.fusion import fuse, render_overall_summary
from pdf_forgery.invoice_arithmetic import InvoiceArithmeticStage
from pdf_forgery.pipeline import run_pipeline_on_path
from pdf_forgery.provenance_metadata import ProvenanceMetadataStage
from pdf_forgery.revision_recovery import RevisionRecoveryStage


# ---------------------------------------------------------------------------
# Change only this value to test another PDF from the ``test_pdf's`` folder.
# A relative/absolute path also works, for example: "samples/my_invoice.pdf".
# ---------------------------------------------------------------------------

PDF_NAME = "tampered.pdf"


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_PDF_DIRECTORY = REPO_ROOT / "test_pdf's"

STAGES = (
    RevisionRecoveryStage(),
    FontForensicsStage(),
    InvoiceArithmeticStage(),
    ProvenanceMetadataStage(),
)


def _resolve_pdf(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    if candidate.exists():
        return candidate.resolve()
    return DEFAULT_PDF_DIRECTORY / candidate


def _print_stage(result) -> None:
    score = "n/a" if result.score is None else str(result.score)
    print(f"\n{'=' * 78}")
    print(f"STAGE: {result.stage}")
    print(f"RESULT: {result.tier.value.upper()} (score {score})")
    print(result.summary)

    if not result.ok:
        print(f"ERROR: {result.error or 'unknown stage error'}")
        return

    for reason in result.reasons:
        print(f"  Reason: {reason}")
    for note in result.notes:
        print(f"  Note: {note}")

    if not result.findings:
        print("  Findings: none")
        return

    print(f"  Findings: {len(result.findings)}")
    for index, finding in enumerate(result.findings, 1):
        page = "unknown" if finding.page is None else str(finding.page + 1)
        print(
            f"\n  [{index}] {finding.tier.value.upper()} | page {page}"
            f" | high-value={finding.high_value or 'none'}"
        )
        print(f"      {finding.reason}")

        if finding.before is not None or finding.after is not None:
            print(f"      before: {finding.before!r}")
            print(f"      after : {finding.after!r}")
        if finding.object_ids:
            print(f"      objects: {', '.join(finding.object_ids)}")
        for evidence in finding.evidence:
            if evidence.before:
                value = f"{evidence.before!r} -> {evidence.after!r}"
            else:
                value = evidence.after
            print(f"      {evidence.label}: {value}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    pdf_path = _resolve_pdf(args[0] if args else PDF_NAME)

    print(f"PDF forgery-detection test harness")
    print(f"File: {pdf_path}")

    if not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}")
        return 2

    results = run_pipeline_on_path(pdf_path, STAGES)
    for result in results:
        _print_stage(result)

    print(f"\n{'=' * 78}")
    print(render_overall_summary(fuse(results)))

    failed = [result.stage for result in results if not result.ok]
    if failed:
        print(f"\nStages that could not run: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
