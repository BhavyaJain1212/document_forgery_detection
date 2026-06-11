"""Command-line entry point for PDF forgery detection (Stage 1).

Usage
-----
    pdf-forgery <path> [--json <out>] [--summary]

Rules (owner-approved, see CLAUDE.md):
  * No flags                -> human-readable summary to stdout.
  * --json <path>           -> write machine-readable JSON to that path
                               (suppresses the stdout summary unless --summary).
  * --summary               -> force the human summary even when --json is set.
  * A directory argument    -> batch mode: ONE combined JSON array, one entry per
                               top-level file (no recursion).
  * Exit code reflects RUN success only, NEVER the verdict. A HIGH finding does
    not change the exit code — confidence is advisory.

Exit codes:
  0  the run completed and produced output (whatever the verdict).
  2  a usage / path error (no such path, nothing to analyse, unwritable --json).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .revision_recovery.analyze import analyze_path
from .revision_recovery.models import AnalysisReport
from .revision_recovery.report import render_json, render_summary

# Top-level PDF discovery for batch mode: top-level only, no recursion.
_PDF_SUFFIX = ".pdf"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-forgery",
        description=(
            "Detect direct-text-editing forgery in PDFs via revision recovery "
            "(Stage 1). Confidence is advisory; a human reviewer decides."
        ),
    )
    parser.add_argument(
        "path",
        help="A PDF file, or a directory to batch-analyse (top-level *.pdf only).",
    )
    parser.add_argument(
        "--json",
        metavar="OUT",
        dest="json_out",
        help="Write machine-readable JSON here ('-' for stdout).",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Force the human-readable summary even when --json is set.",
    )
    return parser


def _discover_pdfs(directory: Path) -> list[Path]:
    """Top-level *.pdf files in a directory, sorted; no recursion."""
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() == _PDF_SUFFIX
    )


def _emit(
    reports: list[AnalysisReport],
    *,
    batch: bool,
    json_out: str | None,
    force_summary: bool,
) -> int:
    """Write JSON and/or summary per the output rules. Returns the exit code."""
    # Batch mode always serialises as an array; single mode as one object.
    json_payload: AnalysisReport | list[AnalysisReport]
    json_payload = reports if batch else reports[0]

    if json_out:
        text = render_json(json_payload)
        if json_out == "-":
            print(text)
        else:
            try:
                Path(json_out).write_text(text, encoding="utf-8")
            except OSError as exc:
                print(f"error: could not write JSON to {json_out!r}: {exc}",
                      file=sys.stderr)
                return 2
            print(f"Wrote JSON report to {json_out}", file=sys.stderr)

    # Human summary: default when no --json, or forced with --summary.
    if not json_out or force_summary:
        for report in reports:
            print(render_summary(report))
            print()

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"error: no such path: {args.path}", file=sys.stderr)
        return 2

    if target.is_dir():
        pdfs = _discover_pdfs(target)
        if not pdfs:
            print(
                f"error: no top-level *.pdf files found in {args.path}",
                file=sys.stderr,
            )
            return 2
        reports = [analyze_path(p) for p in pdfs]
        return _emit(
            reports,
            batch=True,
            json_out=args.json_out,
            force_summary=args.summary,
        )

    report = analyze_path(target)
    return _emit(
        [report],
        batch=False,
        json_out=args.json_out,
        force_summary=args.summary,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
