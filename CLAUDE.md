# PDF Document-Forgery Detection — Stage 1 (Revision Recovery)

This file is the canonical spec + working memory for the project. The spec below
is saved VERBATIM from the project owner. Do not paraphrase or "improve" it.
Two maintained sections follow the spec: **Module layout** and **Progress log**.

---

## Spec (verbatim)

I'm building a PDF document-forgery detection system. This is STAGE 1 of a
larger pipeline; later stages will add font fingerprinting and OCR cross-checks.
For now, build ONLY the stage described below. Plan the architecture first and
show me the plan before writing code.

## Goal
Detect "direct text editing" forgery in PDFs via REVISION RECOVERY. When someone
edits a PDF and saves with an incremental update ("Save", not "Save As"), the
original objects remain in the file and the edit is appended as a new revision.
The detector must recover each historical revision and prove what text changed.

## Detection method (implement exactly this)
1. Read the raw bytes of the input PDF (read-only; never modify the input).
2. Find all %%EOF markers to count revisions. Also note /Prev in trailers and
   multiple xref/startxref sections. Validate candidate boundaries by attempting
   to load each truncation as a real PDF (some %%EOF may sit inside streams).
3. For each valid %%EOF boundary, truncate bytes[0:boundary] and treat that as a
   reconstructed historical revision (each is a complete, loadable PDF).
4. For each pair of consecutive revisions, compare them TWO ways:
   a. TEXT DIFF: extract the text layer per page (deterministic, local) and diff
      consecutive revisions. Report added/removed/changed spans, ignoring pure
      whitespace noise.
   b. OBJECT DIFF: find which PDF objects were overridden between revisions and
      classify each changed object by type: page content stream (/Contents) or
      text object = content change; /Sig = signature; /Annot(s) = annotation;
      /Metadata or XMP = metadata. This classification drives the confidence score.
5. SCORING (output confidence, NOT a binary verdict — a human reviewer makes the
   final call):
   - Multiple revisions + text-layer change in a content stream  -> HIGH confidence
     of text editing. Include the exact before/after text.
   - Multiple revisions but changes only in /Sig, /Annot, or metadata objects
     -> LOW confidence / likely benign (legitimate signing or markup).
   - Single revision -> INCONCLUSIVE for this method; note that later stages
     (font/OCR) are needed.

## Tech stack
- Python 3.11+
- pikepdf (qpdf bindings) for object inspection and per-revision loading
- pdfminer.six or pdfplumber for text-layer extraction
- difflib (stdlib) for diffing
- Keep dependencies minimal. Fully local — no network calls, no cloud APIs
  (the documents are sensitive insurance records).

## Deliverables
1. A Python package with clear modules: revision detection, revision
   reconstruction, text extraction, object-diff/classification, scoring, report.
2. A CLI entry point that takes a PDF path (and supports a directory for batch),
   and emits BOTH machine-readable JSON and a human-readable summary that shows
   the before/after text for any flagged change.
3. A test-fixture generator script: create a clean PDF, then produce a second
   copy that has been edited via an INCREMENTAL UPDATE (text changed, original
   preserved) so I have a known-positive case. Also generate a known-negative
   (single-revision) PDF.
4. Tests that run the detector against both fixtures and assert the expected
   confidence outcomes.

## Engineering constraints
- Treat input files as read-only.
- Handle malformed/encrypted/non-incremental PDFs gracefully — report and
  continue, never crash.
- Make modules independently testable so later stages can plug in.
- Add a short README explaining the method and how to run it.

Start by proposing the module layout and the scoring thresholds, wait for my
confirmation, then implement.

## Scoring rubric (use these exact rules; expose all thresholds as config)

### Text normalization (apply before every diff)
- Unicode NFC; collapse whitespace runs to single space; trim ends.
- Strip zero-width chars (U+200B/C/D) and soft hyphens (U+00AD).
- Case-sensitive. Compare per page, aligned by page index.
- Diff at token level (whitespace split). For changed tokens, also emit a
  character-level diff.

### Substantive change definition
- A diff is "substantive" if, after normalization, there is >=1 added/removed/
  changed token. Whitespace-only differences are NON-substantive.
- There is NO minimum token count. A single-character change counts.

### High-value token patterns (boost confidence when a CHANGED token matches)
- Currency/amount: digits with optional thousands separators/decimals, or
  adjacent to ₹, Rs, INR, $  (e.g. 5,000 / 50000 / ₹1,20,000.00)
- Date: dd/mm/yyyy, dd-mm-yyyy, ISO 8601, or month-name dates
- ID-like: alphanumeric run length >= 6 (policy/claim numbers) -- treat as a
  WEAK booster (noisier), not a strong one.

### Object-change classification (compare consecutive reconstructed revisions)
Classify every overridden object by type:
- page /Contents stream, or text-bearing form XObject -> CONTENT
- /Sig or signature appearance                         -> SIGNATURE
- /Annot(s): comment/highlight/note                    -> MARKUP
- /Annot(s): stamp/redaction OR any annot whose rect   -> OVERLAY
  geometrically overlaps a text region
- form field /V changed, field was empty before        -> FORM_FILL
- form field /V changed, field had a prior value       -> FIELD_EDIT
- /Metadata, XMP, /Info only                            -> META

### Confidence tiers (output tier + score band + the before/after evidence)
- INCONCLUSIVE (n/a): only one revision found. Route to later stages.
- LOW (0-30, likely benign): multiple revisions, but changes are confined to
  SIGNATURE and/or META and/or MARKUP and/or FORM_FILL, with NO substantive
  text change in any CONTENT object.
- MEDIUM (30-70, review): any one of --
    * a CONTENT stream changed but the normalized text diff is empty/whitespace
      only (possible overlay/inpainting the text layer doesn't reflect -> needs
      the later OCR cross-check), OR
    * an OVERLAY object changed, OR
    * a FIELD_EDIT occurred, OR
    * a revision was detected but could not be reconstructed/extracted
      (corruption or evasion -- never silently drop it).
- HIGH (70-100, strong evidence): substantive text diff that maps to a changed
  CONTENT object.
    * changed tokens match a high-value pattern (amount/date) -> 90-100,
      tag "high-value field altered"
    * prose-only changed tokens -> 70-85

### Output
For every flagged change, include: revision indices, the changed object id(s)
and class, the page number, and the exact before -> after text. Confidence is
advisory; a human reviewer decides.

---

## Module layout

> Status: **APPROVED (2026-06-11).** Stage 1 is a subpackage,
> `pdf_forgery/revision_recovery/`, so later stages (`font_fingerprint/`,
> `ocr_crosscheck/`) become sibling subpackages. Implementation underway —
> see the Progress log for what exists vs. proposed.

Package name: `pdf_forgery` (under `src/`). Python `>=3.11` (local interpreter
is 3.12; no 3.11-specific build available on this machine — 3.12 satisfies the
constraint).

```
document_forgery_detection/
├── CLAUDE.md                  # this file: spec + layout + progress log
├── README.md                  # short: method + how to run
├── pyproject.toml             # src layout, deps: pikepdf, pdfminer.six, pdfplumber
├── .gitignore
├── src/
│   └── pdf_forgery/
│       ├── __init__.py        # version
│       ├── cli.py             # shared entry point; for now dispatches to stage 1.
│       │                      #   Rules: no flags -> human summary to stdout;
│       │                      #   --json <path> writes machine JSON; --summary
│       │                      #   forces the summary even with --json; a directory
│       │                      #   arg -> batch, ONE combined JSON array (one entry
│       │                      #   per file, top-level only, no recursion); exit code
│       │                      #   reflects RUN success only, NEVER the verdict.
│       └── revision_recovery/         # STAGE 1 (this stage)
│           ├── __init__.py            # stage facade: analyze_path() re-export
│           ├── config.py              # Config dataclass: ALL thresholds, score
│           │                          #   bands, high-value regexes, normalization
│           │                          #   toggles. Nothing magic hard-coded outside.
│           ├── models.py              # dataclasses: EOFMarker, XrefSection,
│           │                          #   RevisionBoundary, DetectionResult,
│           │                          #   Revision, ObjectChange, TextChange,
│           │                          #   Finding, AnalysisReport. Pure data.
│           ├── detect.py              # scan raw bytes: %%EOF markers, /Prev chain,
│           │                          #   xref/startxref sections -> candidate
│           │                          #   RevisionBoundary list. Cheap STRUCTURAL
│           │                          #   validity flag (boundary must end with
│           │                          #   'startxref <n>'); in-stream %%EOF kept
│           │                          #   but valid=False. No PDF loading here —
│           │                          #   authoritative load-test is reconstruct.
│           ├── reconstruct.py         # truncate bytes[0:boundary]; validate each
│           │                          #   by loading with pikepdf; emit Revision
│           │                          #   objects. A detected-but-unloadable
│           │                          #   boundary is kept and flagged (never
│           │                          #   silently dropped).
│           ├── extract/
│           │   ├── __init__.py
│           │   ├── text.py            # per-page text-layer extraction via
│           │   │                      #   pdfminer.six (deterministic); the text
│           │   │                      #   diff uses ONLY this.
│           │   ├── words.py           # pdfplumber word-level bounding boxes,
│           │   │                      #   used ONLY by the OVERLAY check (annot
│           │   │                      #   rect overlapping a text region).
│           │   └── normalize.py       # NFC, collapse whitespace, strip
│           │                          #   ZW/soft-hyphen, trim; tokenizer. Shared
│           │                          #   so text + object diffs normalize alike.
│           ├── diff/
│           │   ├── __init__.py
│           │   ├── textdiff.py        # difflib token-level diff per page (aligned
│           │   │                      #   by page index) + char-level diff on
│           │   │                      #   changed tokens; substantive vs whitespace.
│           │   └── objectdiff.py      # overridden-object detection between
│           │                          #   consecutive revisions + classification:
│           │                          #   CONTENT / SIGNATURE / MARKUP / OVERLAY /
│           │                          #   FORM_FILL / FIELD_EDIT / META. OVERLAY
│           │                          #   geometry uses extract/words.py.
│           ├── highvalue.py           # high-value token matchers (amount/date
│           │                          #   strong; ID-like weak). Config-driven.
│           ├── scoring.py             # combine text + object diffs -> tier + score
│           │                          #   band + evidence per rubric. Config thresholds.
│           └── report.py              # render AnalysisReport -> JSON (machine) and
│                                      #   a human summary showing before -> after.
├── scripts/
│   └── make_fixtures.py       # generate: clean.pdf, edited_incremental.pdf
│                              #   (known-positive, text changed via incremental
│                              #   update, original preserved), single_rev.pdf
│                              #   (known-negative). Deterministic output.
└── tests/
    ├── conftest.py            # build fixtures into a tmp dir once per session
    ├── test_detect.py         # revision detection over synthetic raw bytes
    ├── test_reconstruct.py    # truncate + load-validate; unloadable kept & flagged
    ├── test_objectdiff.py     # classification of overridden objects
    ├── test_textdiff.py       # normalization + substantive-change detection
    ├── test_scoring.py        # tier/score boundaries from the rubric
    └── test_end_to_end.py     # detector vs fixtures: positive -> HIGH,
                               #   negative -> INCONCLUSIVE
```

### Data flow (one analysis)
`cli` → read raw bytes → `revisions.detect` (boundaries) →
`revisions.reconstruct` (loadable Revision per boundary) → for each consecutive
pair: `extract.text` + `extract.normalize` → `diff.textdiff` and
`diff.objectdiff` (+`highvalue`) → `scoring` (tier + score) →
`report` (JSON + human summary).

### Why this split
- **Stage 1 is a subpackage** (`revision_recovery/`); later stages
  (`font_fingerprint/`, `ocr_crosscheck/`) are siblings under `pdf_forgery/`.
- **Each stage is a pure function over data models** (`models.py`), so later
  stages plug in at the diff/scoring layer without touching revision recovery.
- **All thresholds and regexes live in `config.py`** — the rubric demands every
  threshold be configurable.
- **`normalize.py` is shared** so text-diff and object-diff can't drift apart.
- **Reconstruction never drops a detected revision**: an unloadable boundary
  becomes a flagged Finding (feeds the MEDIUM "detected but not reconstructed"
  rule), satisfying "never silently drop it."

### Resolved decisions (owner-approved 2026-06-11)
1. **Package name** `pdf_forgery`; Stage 1 lives in `pdf_forgery/revision_recovery/`.
2. **Text extraction** is **pdfminer.six** only, for deterministic output — it
   drives the text diff. **pdfplumber** stays a dependency but is used *only* in
   `extract/words.py` for word-level bounding boxes feeding the OVERLAY check
   (annotation rect overlapping a text region). It is never used for the text diff.
3. **CLI** (`pdf-forgery <path>`):
   - No flags → human-readable summary to **stdout**.
   - `--json <path>` → write machine-readable JSON to that path.
   - `--summary` → force the human summary even when `--json` is set.
   - **Directory arg → batch**: ONE combined JSON array, one entry per file;
     top-level only, **no recursion**; not scattered per-file outputs.
   - **Exit code reflects RUN success only, never the verdict.** A HIGH finding
     does not change the exit code (confidence is advisory).

---

## Progress log

A running checklist, updated at the end of every task.

- [x] **Stage 1 / Task 0 — Project bootstrap** (2026-06-11)
  - [x] CLAUDE.md created; spec saved verbatim.
  - [x] `pyproject.toml` (src layout; deps: pikepdf, pdfminer.six, pdfplumber).
  - [x] `.gitignore` added.
  - [x] `src/pdf_forgery/__init__.py` created (package only; no logic).
  - [x] Short `README.md` (method + how to run).
  - [x] `git init` + initial commit.
  - [x] Module layout PROPOSED in this file.
  - [x] Owner reviewed & APPROVED layout (2026-06-11) with 3 refinements:
    stage-1 subpackage `revision_recovery/`; pdfminer for text / pdfplumber only
    for OVERLAY word boxes; CLI output/exit-code rules. Layout updated.
- [x] **Stage 1 / Task 1 — revision detection (`detect.py`)** (2026-06-11)
  - [x] `revision_recovery/` subpackage created; `models.py` with detection
    dataclasses (`EOFMarker`, `XrefSection`, `RevisionBoundary`, `DetectionResult`).
  - [x] `detect.py`: pure raw-byte scan — every `%%EOF`, `startxref`+pointer,
    `/Prev` chain → ordered candidate `RevisionBoundary` list with byte offsets.
  - [x] Cheap STRUCTURAL validity flag: a clean boundary ends with
    `startxref <n>` right before `%%EOF`; an in-stream `%%EOF` lacks that tail →
    kept but `valid=False` (never dropped). Authoritative pikepdf load-test still
    belongs to `reconstruct.py`. `revision_count`/`is_multi_revision` count only
    VALID boundaries; `candidate_count`/`valid_boundaries` expose both views.
    Verified the heuristic against real qpdf output (`startxref\n<n>\n%%EOF`).
  - [x] Read-only `detect_from_path`; empty/headerless/marker-less/missing-file
    inputs reported via `notes`, never raise.
  - [x] `tests/test_detect.py` — 18 tests over synthetic bytes (single/multi
    revision, in-stream `%%EOF` flagged invalid, EOL handling, read-only
    guarantee). All pass.
  - [x] Dev env: `.venv` (Python 3.12) with deps installed; package editable-installed.
  - Note: system `pip` is Python 3.10 and PEP-668 externally-managed — use
    `./.venv/bin/python -m pytest` to run the suite.
- [x] **Stage 1 / Task 2 — revision reconstruction (`reconstruct.py`)** (2026-06-11)
  - [x] `reconstruct(raw, detection)`: for each VALID boundary, truncate
    `raw[0:truncate_len]` and load with pikepdf; returns ordered `Revision` list
    (re-indexed from 0, with page_count + is_encrypted), earliest first.
  - [x] Authoritative load gate: a boundary that won't open becomes a
    `ReconstructionFailure` (reported + skipped, never crashes, never dropped) —
    feeds the MEDIUM "detected but not reconstructed" rule. Cases handled:
    corrupt/unloadable (`pikepdf.PdfError`), encrypted-password-required
    (`pikepdf.PasswordError`), and any unexpected error (broad catch).
    Empty-user-password encryption opens and is flagged `is_encrypted=True`.
  - [x] Models: `Revision`, `ReconstructionFailure`, `ReconstructionResult`
    (with `revision_count`/`has_failures`/`is_multi_revision`).
  - [x] Read-only `reconstruct_from_path`; missing/dir/unreadable reported via notes.
  - [x] `tests/test_reconstruct.py` — 12 tests using REAL pikepdf-built PDFs incl.
    a genuine incremental-update builder (`_append_incremental`: appends obj +
    xref + `/Prev` trailer + startxref + `%%EOF`). Covers 1- & 2-revision load,
    exact-truncation + independent loadability, unloadable/encrypted failures,
    invalid-boundary skip, read-only guarantee. Suite: 30 pass.
- [ ] Stage 1 / Task 3 — `extract/text.py` (pdfminer) + `extract/normalize.py`
      + `extract/words.py` (pdfplumber, OVERLAY only)
- [ ] Stage 1 / Task 4 — `diff/textdiff.py` + `diff/objectdiff.py` + `highvalue.py`
- [ ] Stage 1 / Task 5 — `config.py` + `scoring.py`
- [ ] Stage 1 / Task 6 — `report.py` + `cli.py`
- [ ] Stage 1 / Task 7 — `scripts/make_fixtures.py` (known +/- cases)
- [ ] Stage 1 / Task 8 — end-to-end tests (HIGH on positive, INCONCLUSIVE on negative)
