# PDF Forgery Detection — Stage 1: Revision Recovery

Detects **direct text-editing forgery** in PDFs by recovering historical
revisions left behind by *incremental updates*. When a PDF is edited and saved
with "Save" (not "Save As"), the original objects stay in the file and the edit
is appended as a new revision marked by an extra `%%EOF`. This stage recovers
each revision and proves what text changed between them.

Fully local — **no network calls, no cloud APIs** (the target documents are
sensitive insurance records). Input files are treated as strictly read-only.

> **Status:** Stage 1 is partially implemented: revision detection,
> reconstruction, extraction, text diff, and object diff are in place. Scoring,
> reports, fixtures, and the CLI are not wired up yet.

## Method (summary)

1. Read the raw PDF bytes (read-only).
2. Find every `%%EOF` marker, the `/Prev` trailer chain, and the
   `xref`/`startxref` sections to enumerate candidate revision boundaries.
3. Validate each boundary by truncating `bytes[0:boundary]` and loading it as a
   real PDF (some `%%EOF` markers live inside streams and are not real
   boundaries).
4. For each consecutive revision pair, diff two ways:
   - **Text diff** — extract the per-page text layer, normalize, and diff at
     token + character level, ignoring whitespace-only noise.
   - **Object diff** — find overridden PDF objects and classify them
     (CONTENT / SIGNATURE / MARKUP / OVERLAY / FORM_FILL / FIELD_EDIT / META).
5. **Score** a confidence tier (INCONCLUSIVE / LOW / MEDIUM / HIGH) with the
   exact before → after evidence. The score is advisory — a human reviewer makes
   the final call. This is Stage 1 of a larger pipeline; later stages add font
   fingerprinting and OCR cross-checks.

The full scoring rubric and thresholds are in [`CLAUDE.md`](./CLAUDE.md).

## Install

Requires Python 3.11+ (3.12 works).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage (planned)

```bash
# Single file
pdf-forgery path/to/document.pdf --summary --json result.json

# Batch a directory
pdf-forgery path/to/folder/ --json results/
```

## Develop

```bash
python scripts/make_fixtures.py   # build known-positive / known-negative PDFs
pytest                            # run the test suite
```
