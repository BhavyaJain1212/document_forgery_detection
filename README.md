# PDF Forgery Detection — Stage 1: Revision Recovery

Detects **direct text-editing forgery** in PDFs by recovering historical
revisions left behind by *incremental updates*. When a PDF is edited and saved
with "Save" (not "Save As"), the original objects stay in the file and the edit
is appended as a new revision marked by an extra `%%EOF`. This stage recovers
each revision and proves what text changed between them.

Fully local — **no network calls, no cloud APIs** (the target documents are
sensitive insurance records). Input files are treated as strictly read-only.

> **Status:** Stage 1 is complete and end-to-end tested — revision detection,
> reconstruction, text + object diff, scoring, JSON/human reports, the CLI, and
> the fixture generator are all in place (246 passing tests). Later stages (font
> fingerprinting, OCR cross-check) are not yet built.

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

## Usage

```bash
# Human-readable summary to stdout (default; shows before -> after for each change)
pdf-forgery path/to/document.pdf

# Machine-readable JSON to a file ('-' writes JSON to stdout)
pdf-forgery path/to/document.pdf --json result.json

# JSON plus the human summary (force the summary even with --json)
pdf-forgery path/to/document.pdf --json result.json --summary

# Batch a directory: ONE combined JSON array, top-level *.pdf only (no recursion)
pdf-forgery path/to/folder/ --json results.json
```

**Output rules**

- No flags → human summary to stdout.
- `--json <out>` → machine JSON written there (`-` = stdout); the summary is
  suppressed unless `--summary` is also given.
- A directory argument → batch mode: one JSON array, one entry per top-level
  `*.pdf` (no recursion).
- **The exit code reflects run success only, never the verdict** — a HIGH
  finding still exits `0`. Usage/path errors exit `2`.

### Confidence tiers

| Tier | Meaning |
| --- | --- |
| `INCONCLUSIVE` | Only one revision — this method can't decide; route to later stages. |
| `LOW` (0–30) | Multiple revisions, but changes are benign (signature / metadata / markup / form-fill). |
| `MEDIUM` (30–70) | Content changed with no text diff (possible overlay), an overlay/field edit, or a revision that couldn't be reconstructed. Review. |
| `HIGH` (70–100) | Substantive text change in a content stream — strong evidence of direct text editing. Amount/date edits score 90–100. |

Confidence is **advisory**; a human reviewer makes the final call.

## Develop

```bash
python scripts/make_fixtures.py   # build known-positive / known-negative PDFs into tests/fixtures/
pytest                            # run the test suite (246 tests)
```

The fixture generator produces `clean.pdf` (single-revision **known-negative** →
INCONCLUSIVE) and `edited_incremental.pdf` (a genuine incremental update that
alters a currency amount, **known-positive** → HIGH). Output is deterministic.
